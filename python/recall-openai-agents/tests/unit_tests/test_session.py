"""RecallSession over a fake Recall client, inside real Agents SDK runs."""

import pytest
from agents import Agent, Runner
from agents.memory import Session
from agents.testing import ScriptedModel, assistant_message

from recall_openai_agents import RecallSession
from tests.unit_tests.fake_agents import FakeAgentsClient


def test_it_is_a_session():
    with RecallSession("s1", client=FakeAgentsClient()) as session:
        assert isinstance(session, Session)
        assert session.session_id == "s1"
        assert session.agent_id == "oa-s1"
    assert RecallSession("tickets/42", client=FakeAgentsClient()).agent_id.startswith("oa-")


async def test_the_second_run_sees_the_first():
    client = FakeAgentsClient()
    model = ScriptedModel()
    agent = Agent(name="test", model=model)
    with RecallSession("s1", client=client) as session:
        model.enqueue([assistant_message("San Francisco")])
        first = await Runner.run(agent, "What city is the Golden Gate Bridge in?", session=session)
        assert first.final_output == "San Francisco"
        model.enqueue([assistant_message("California")])
        second = await Runner.run(agent, "What state is it in?", session=session)
        assert second.final_output == "California"
        assert len(model.calls[-1].input) == 3
        items = await session.get_items()
        assert len(items) == 4
        assert items[0]["content"] == "What city is the Golden Gate Bridge in?"
        assert await session.get_items(limit=1) == items[-1:]
        assert await session.get_items(limit=0) == []
    # Every message is a turn of the Recall agent as well.
    assert [(t.role, t.content) for t in client.turns["oa-s1"]] == [
        ("user", "What city is the Golden Gate Bridge in?"), ("assistant", "San Francisco"),
        ("user", "What state is it in?"), ("assistant", "California"),
    ]
    assert client.held == {}


async def test_another_process_reads_the_same_session():
    client = FakeAgentsClient()
    with RecallSession("s2", client=client) as session:
        await session.add_items([{"role": "user", "content": "hi"}])
    with RecallSession("s2", client=client) as session:
        assert await session.get_items() == [{"role": "user", "content": "hi"}]


async def test_pop_and_clear():
    client = FakeAgentsClient()
    with RecallSession("s3", client=client) as session:
        assert await session.pop_item() is None
        await session.add_items([{"role": "user", "content": "one"}, {"role": "assistant", "content": "two"}])
        assert (await session.pop_item())["content"] == "two"
        assert await session.get_items() == [{"role": "user", "content": "one"}]
        await session.add_items([{"role": "user", "content": "three"}])
        await session.clear_session()
        assert await session.get_items() == []
        assert await session.pop_item() is None
        # The record of what was said is kept, in order.
        assert [t.content for t in client.turns["oa-s3"]] == ["one", "two", "three"]
        # New items after a clear are new turns, with new ids.
        await session.add_items([{"role": "user", "content": "four"}])
        assert [t.message_id for t in client.turns["oa-s3"]][-1] == "s3:4"


async def test_tool_calls_and_other_items_are_recorded_or_skipped():
    client = FakeAgentsClient()
    with RecallSession("s4", client=client) as session:
        await session.add_items([
            {"type": "function_call", "call_id": "c1", "name": "lookup", "arguments": '{"id": 7}'},
            {"type": "function_call_output", "call_id": "c1", "output": "order 7: shipped"},
            {"type": "reasoning", "id": "r1", "summary": []},
            {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "Shipped."}]},
        ])
        assert len(await session.get_items()) == 4
    assert [(t.role, t.content, t.name) for t in client.turns["oa-s4"]] == [
        ("assistant", 'lookup({"id": 7})', "lookup"), ("tool", "order 7: shipped", "c1"),
        ("assistant", "Shipped.", ""),
    ]


async def test_turns_can_be_left_out():
    client = FakeAgentsClient()
    with RecallSession("s5", client=client, record_turns=False) as session:
        await session.add_items([{"role": "user", "content": "hi"}])
    assert "oa-s5" not in client.turns


def test_an_empty_session_id_is_refused():
    with pytest.raises(ValueError):
        RecallSession("", client=FakeAgentsClient())
