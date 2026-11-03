"""RecallSaver over the fake client: LangGraph's checkpointer conformance
suite, then a real graph with interrupts."""

import operator
from typing import Annotated, TypedDict

import pytest
from langgraph.checkpoint.conformance import checkpointer_test, validate
from langgraph.checkpoint.conformance.report import ProgressCallbacks
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from polign_recall import RecallError

from recall_langgraph import RecallSaver
from tests.unit_tests.fake_agents import FakeAgentsClient


@checkpointer_test(name="RecallSaver (fake client)")
async def fake_saver():
    with RecallSaver(client=FakeAgentsClient()) as saver:
        yield saver


async def test_conformance():
    report = await validate(fake_saver, progress=ProgressCallbacks.verbose())
    report.print_report()
    assert report.passed_all_base()


class State(TypedDict):
    steps: Annotated[list[str], operator.add]
    answer: str


def build(saver):
    def ask(state):
        reply = interrupt("What is the answer?")
        return {"steps": ["asked"], "answer": reply}

    def finish(state):
        return {"steps": ["finished"]}

    builder = StateGraph(State)
    builder.add_node("ask", ask)
    builder.add_node("finish", finish)
    builder.add_edge(START, "ask")
    builder.add_edge("ask", "finish")
    builder.add_edge("finish", END)
    return builder.compile(checkpointer=saver)


def test_a_graph_interrupts_and_resumes_through_the_saver():
    client = FakeAgentsClient()
    config = {"configurable": {"thread_id": "support/ticket 42"}}
    with RecallSaver(client=client) as saver:
        graph = build(saver)
        first = graph.invoke({"steps": [], "answer": ""}, config)
        assert first["__interrupt__"][0].value == "What is the answer?"
        assert graph.get_state(config).next == ("ask",)
    # Another process, another saver, same records.
    with RecallSaver(client=client) as saver:
        graph = build(saver)
        final = graph.invoke(Command(resume="forty-two"), config)
        assert final["answer"] == "forty-two"
        assert final["steps"] == ["asked", "finished"]
        history = list(graph.get_state_history(config))
        assert len(history) >= 3
        assert history[-1].next == ("__start__",)
    # Every lease taken for a write was handed back.
    assert client.held == {}


def test_the_thread_id_becomes_a_valid_agent_id():
    from recall_langgraph.saver import THREAD_PREFIX
    from recall_langgraph._records import agent_name
    assert agent_name(THREAD_PREFIX, "t1") == "lg-t1"
    hashed = agent_name(THREAD_PREFIX, "support/ticket 42")
    assert hashed.startswith("lg-") and len(hashed) == 43
    assert agent_name(THREAD_PREFIX, "x" * 200) != "lg-" + "x" * 200


def test_a_write_waits_for_the_lease_and_then_gives_up():
    client = FakeAgentsClient()
    holder = FakeAgentsClient()
    holder.outputs = client.outputs
    holder.held = client.held  # the lease lives in the database, shared by both
    held = holder.resume("lg-t1")
    with RecallSaver(client=client, lease_wait=0.3) as saver:
        with pytest.raises(RecallError) as caught:
            saver.put({"configurable": {"thread_id": "t1", "checkpoint_ns": ""}},
                      {"v": 1, "id": "1", "ts": "", "channel_values": {}, "channel_versions": {},
                       "versions_seen": {}}, {"source": "input", "step": -1}, {})
        assert caught.value.code == "lease_held"
        held.release()
        saver.put({"configurable": {"thread_id": "t1", "checkpoint_ns": ""}},
                  {"v": 1, "id": "1", "ts": "", "channel_values": {}, "channel_versions": {},
                   "versions_seen": {}}, {"source": "input", "step": -1}, {})
        assert saver.get_tuple({"configurable": {"thread_id": "t1"}}).checkpoint["id"] == "1"


def test_listing_every_thread_is_refused():
    with RecallSaver(client=FakeAgentsClient()) as saver:
        with pytest.raises(ValueError):
            list(saver.list(None))
