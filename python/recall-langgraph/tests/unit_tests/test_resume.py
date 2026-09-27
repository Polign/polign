"""RecallResume and recall_tools over a fake Recall client, inside real LangGraph graphs."""

import json
import warnings

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import START, MessagesState, StateGraph
from polign_recall import RecallError, WorkingState

from recall_langgraph import BRIEFING_ID, RecallResume, recall_tools
from tests.scripted import ScriptedChatModel, seen_text

with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    from langgraph.prebuilt import create_react_agent


def react_agent(model, resume, **kwargs):
    with warnings.catch_warnings():
        # create_react_agent is deprecated in favor of langchain.agents.create_agent,
        # but it is still the prebuilt agent LangGraph ships.
        warnings.simplefilter("ignore")
        return create_react_agent(model, recall_tools(resume), pre_model_hook=resume.pre_model_hook,
                                  post_model_hook=resume.post_model_hook, **kwargs)


def turns(client):
    return [(t.role, t.content, t.name) for t in client.turns]


def test_the_pre_model_hook_puts_the_briefing_in_front_without_changing_state(client):
    client.state = WorkingState(goal="port billing to v2")
    resume = RecallResume("coder-1", client=client, token_budget=4000, lease_ttl=30)
    state = {"messages": [HumanMessage("carry on", id="h1")]}
    update = resume.pre_model_hook.invoke(state)
    assert set(update) == {"llm_input_messages"}
    first, second = update["llm_input_messages"]
    assert isinstance(first, SystemMessage) and first.id == BRIEFING_ID
    assert "port billing to v2" in first.content
    assert second.content == "carry on"
    assert len(state["messages"]) == 1
    assert turns(client) == [("user", "carry on", "")]
    assert client.resumes == [{"agent_id": "coder-1", "token_budget": 4000, "lease_ttl": 30,
                               "holder": None, "output_threshold": None}]
    # the briefing is not added twice
    assert resume.with_briefing(update["llm_input_messages"]) == update["llm_input_messages"]


def test_each_message_is_recorded_once_in_order(client):
    resume = RecallResume("coder-1", client=client)
    messages = [
        HumanMessage("find the charge calls", id="h1"),
        AIMessage("", id="a1", tool_calls=[{"name": "grep", "args": {"pattern": "charge("}, "id": "c1"}]),
        ToolMessage("billing.py:12", tool_call_id="c1", name="grep", id="t1"),
        AIMessage([{"type": "text", "text": "Found one."}], id="a2"),
        SystemMessage("be brief", id="s1"),
        resume.briefing_message(),
        HumanMessage([{"type": "image_url", "image_url": {"url": "https://x/y.png"}}], id="h2"),
        AIMessage("Checking.", id="a3", tool_calls=[{"name": "grep", "args": {}, "id": "c2"}]),
    ]
    assert resume.record(messages[:3]) == 3
    assert resume.record(messages) == 4
    assert resume.record(messages) == 0
    calls = 'grep(pattern="charge(")'
    assert turns(client) == [
        ("user", "find the charge calls", ""),
        ("assistant", calls, ""),
        ("tool", "billing.py:12", "grep"),
        ("assistant", "Found one.", ""),
        ("system", "be brief", ""),
        ("user", "[image_url]", ""),
        ("assistant", "Checking.\ngrep()", ""),
    ]
    # a message built by hand without an id is told apart by its content
    assert resume.record([HumanMessage("no id")]) == 1
    assert resume.record([HumanMessage("no id")]) == 0


def test_long_tool_arguments_are_shortened_only_in_the_brief(client):
    resume = RecallResume("coder-1", client=client)
    body = "import billing\n" * 40
    call = {"name": "write_file", "args": {"path": "a.py", "content": body}, "id": "c9"}
    resume.record([AIMessage("", id="a9", tool_calls=[call]), AIMessage("", id="a10",
                   tool_calls=[{"name": "read_file", "args": {"path": "a.py"}, "id": "c10"}])])
    written, read = client.turns
    assert written.content == f"write_file(path=\"a.py\", content={json.dumps(body)})"
    assert written.brief == f'write_file(path="a.py", content=<{len(body)} chars>)'
    assert "c9" not in written.content  # call ids are useless to the next model
    assert read.brief == ""  # short calls need no separate brief


def test_a_restored_thread_is_not_recorded_twice(client):
    # The first process records a thread, then dies.
    first = RecallResume("coder-1", client=client)
    thread = [HumanMessage("find the charge calls", id="h1"), AIMessage("On it.", id="a1"),
              HumanMessage("and the refund calls", id="h2")]
    first.record(thread[:2])
    # h2 reached the state but the process died before recording it.
    first.release()

    # The next process resumes on the same thread id: the checkpointer
    # restores all three messages, and the caller adds one more.
    second = RecallResume("coder-1", client=client)
    restored = [*thread, HumanMessage("then open a PR", id="h3")]
    assert second.record(restored) == 2
    assert turns(client) == [
        ("user", "find the charge calls", ""),
        ("assistant", "On it.", ""),
        ("user", "and the refund calls", ""),
        ("user", "then open a PR", ""),
    ]
    assert [t.message_id for t in client.turns] == ["h1", "a1", "h2", "h3"]
    assert second.record(restored) == 0


def test_a_new_thread_after_resume_records_its_first_input(client):
    first = RecallResume("coder-1", client=client)
    first.record([HumanMessage("hello", id="h1")])
    first.release()
    second = RecallResume("coder-1", client=client)
    assert second.record([HumanMessage("carry on", id="n1")]) == 1


def test_a_held_agent_raises_lease_held_and_release_hands_it_over(client):
    with RecallResume("coder-1", client=client) as first:
        with pytest.raises(RecallError) as caught:
            RecallResume("coder-1", client=client).open()
        assert caught.value.code == "lease_held"
        first.record([HumanMessage("still mine", id="h1")])
        agent = first.agent
    first.release()
    assert agent.released == 1
    with pytest.raises(RuntimeError):
        first.open()
    with RecallResume("coder-1", client=client) as later:
        assert "still mine" in later.briefing
        assert later.context.turn_seq == 1
    assert not client.closed  # a client passed in is the caller's to close


def test_an_owned_client_is_closed_on_release(client, monkeypatch):
    opened = []

    def fake_client(**options):
        opened.append(options)
        return client

    monkeypatch.setattr("recall_langgraph.resume.Client", fake_client)
    with RecallResume("coder-1", local_dir="./data") as resume:
        assert resume.briefing
    assert opened == [{"agent": True, "local_dir": "./data", "env": None}]
    assert client.closed
    with pytest.raises(ValueError):
        RecallResume("coder-1", client=client, env={})


async def test_async_open_and_release(client):
    async with RecallResume("coder-1", client=client) as resume:
        update = await resume.pre_model_hook.ainvoke({"messages": [HumanMessage("hi", id="h1")]})
        assert update["llm_input_messages"][0].id == BRIEFING_ID
        assert await resume.arecord([HumanMessage("hi", id="h1")]) == 0
    assert client.held_by is None


async def test_tools_write_through_the_resume(client):
    resume = RecallResume("coder-1", client=client)
    tools = {t.name: t for t in recall_tools(resume)}
    assert sorted(tools) == ["fetch_output", "milestone", "set_pointer", "update_working_state"]

    assert tools["update_working_state"].invoke({"goal": "port billing", "plan": ["a", "b"]}) \
        == "Working state saved (version 1)."
    await tools["update_working_state"].ainvoke({"progress": "a done"})
    assert client.state.goal == "port billing" and client.state.progress == "a done"
    assert client.state.plan == ("a", "b")

    assert tools["milestone"].invoke({"name": "tests pass"}) == "Milestone recorded: tests pass."
    assert client.state.last_milestone == "tests pass"

    assert tools["fetch_output"].invoke({"ref": "out-1"}) == "the whole grep output"
    assert "no output" in tools["fetch_output"].invoke({"ref": "missing"})  # errors go back as text

    out = tools["set_pointer"].invoke({"name": "wip", "type": "git_ref", "repo": "github.com/acme/billing",
                                       "branch": "agent/wip", "note": "the work so far"})
    assert out == "Pointer wip saved."
    pointer = client.pointers["wip"]
    assert pointer.fields == {"repo": "github.com/acme/billing", "branch": "agent/wip"}
    assert pointer.note == "the work so far"


def test_a_react_agent_starts_from_the_briefing_and_records_every_message_once(client):
    client.state = WorkingState(goal="port billing to v2")
    model = ScriptedChatModel([
        ("update_working_state", {"progress": "found 3 call sites"}),
        "Three call sites so far.",
        "Next is billing.py.",
    ])
    with RecallResume("coder-1", client=client) as resume:
        graph = react_agent(model, resume, prompt="Migrate billing.", checkpointer=InMemorySaver())
        config = {"configurable": {"thread_id": "run-1"}}
        result = graph.invoke({"messages": [("user", "Carry on.")]}, config)
        assert result["messages"][-1].content == "Three call sites so far."
        assert all(m.id != BRIEFING_ID for m in result["messages"])  # the state never holds it

        first = model.inputs[0]
        assert [m.type for m in first] == ["system", "system", "human"]
        assert first[0].content == "Migrate billing."
        assert first[1].id == BRIEFING_ID and "port billing to v2" in first[1].content
        assert client.state.progress == "found 3 call sites"
        # the model call after the tool also gets the briefing
        assert model.inputs[1][1].id == BRIEFING_ID

        # a second turn on the same thread records only what is new
        graph.invoke({"messages": [("user", "What next?")]}, config)
        assert "Three call sites so far." in seen_text(model.inputs[-1])

    assert [(role, name) for role, _, name in turns(client)] == [
        ("user", ""), ("assistant", ""), ("tool", "update_working_state"), ("assistant", ""),
        ("user", ""), ("assistant", ""),
    ]
    assert client.turns[2].content == "Working state saved (version 1)."
    assert client.turns[1].content.startswith("update_working_state(")


def test_a_react_agent_resumed_on_its_old_thread_records_only_new_messages(client):
    # The checkpointer outlives the process, as a Postgres or SQLite one would.
    saver = InMemorySaver()
    config = {"configurable": {"thread_id": "run-1"}}
    # One scripted model for both processes, so its reply ids stay unique
    # the way a real provider's are.
    model = ScriptedChatModel(["Looking.", "Found three."])
    with RecallResume("coder-1", client=client) as resume:
        graph = react_agent(model, resume, checkpointer=saver)
        graph.invoke({"messages": [("user", "Find the charge calls.")]}, config)
    assert len(client.turns) == 2

    # A new process resumes the agent and keeps using the same thread.
    with RecallResume("coder-1", client=client) as resume:
        graph = react_agent(model, resume, checkpointer=saver)
        result = graph.invoke({"messages": [("user", "Any luck?")]}, config)
        assert len(result["messages"]) == 4  # the checkpointer restored the first two

    assert [c for _, c, _ in turns(client)] == ["Find the charge calls.", "Looking.", "Any luck?", "Found three."]


async def test_a_react_agent_works_async_too(client):
    model = ScriptedChatModel([("milestone", {"name": "tests pass"}), "Done."])
    async with RecallResume("coder-1", client=client) as resume:
        graph = react_agent(model, resume)
        result = await graph.ainvoke({"messages": [("user", "Run the tests.")]})
        assert result["messages"][-1].content == "Done."
    assert client.state.last_milestone == "tests pass"
    assert [role for role, _, _ in turns(client)] == ["user", "assistant", "tool", "assistant"]


def test_a_custom_state_graph_with_the_briefing_and_record_nodes(client):
    client.state = WorkingState(goal="write the report")
    model = ScriptedChatModel(["Drafting the summary."])
    with RecallResume("writer-1", client=client) as resume:
        builder = StateGraph(MessagesState)
        builder.add_node("briefing", resume.briefing_node)
        builder.add_node("model", lambda state: {"messages": [model.invoke(state["messages"])]})
        builder.add_node("record", resume.record_node)
        builder.add_edge(START, "briefing")
        builder.add_edge("briefing", "model")
        builder.add_edge("model", "record")
        graph = builder.compile(checkpointer=InMemorySaver())
        config = {"configurable": {"thread_id": "t"}}
        result = graph.invoke({"messages": [("user", "Start.")]}, config)
        graph.invoke({"messages": [("user", "Go on.")]}, config)

    assert result["messages"][0].id == BRIEFING_ID
    assert "write the report" in model.inputs[0][0].content
    assert [m.id for m in model.inputs[1]].count(BRIEFING_ID) == 1  # added once per thread
    assert turns(client) == [
        ("user", "Start.", ""), ("assistant", "Drafting the summary.", ""),
        ("user", "Go on.", ""), ("assistant", "Okay.", ""),
    ]
