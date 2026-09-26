"""RecallAgent(resume=...) through a real AgentSession in text mode, over the fake subprocess."""

import asyncio
import time

import pytest
from livekit.agents.voice import AgentSession

from recall_livekit import AgentResume, RecallAgent

from tests.scripted import ScriptedLLM


def turns(memory, agent_id):
    # Read back what the fake recorded, as the next worker would see it.
    return [(t["role"], t["content"], t.get("name", ""))
            for t in memory.client._tool("recent_turns", {"agent_id": agent_id, "limit": 50})]


async def test_a_resumed_call_records_its_turns_and_releases_on_close(agent_memory):
    sam = agent_memory.for_subject("sam")
    model = ScriptedLLM([
        ("update_working_state", {"goal": "fix the router", "focus": "checking the lights"}),
        "Let's check the lights on your router.",
    ])
    async with AgentSession(llm=model) as session:
        agent = RecallAgent(memory=sam, instructions="Support line.", resume="room-1")
        await session.start(agent)
        await agent.wait_for_memory()
        assert agent.resume.resumed and agent.resume.context.fresh
        assert sorted(t.info.name for t in agent.tools) == ["remember", "update_working_state"]
        # a fresh call gets the tool hint, not a briefing
        assert "update_working_state tool" in str(agent.instructions)
        assert "cut off" not in str(agent.instructions)
        assert str(agent.instructions).index("<recall_memory>") < str(agent.instructions).index("<recall_resume>")

        result = await session.run(user_input="My wifi keeps dropping")

    result.expect.next_event().is_function_call(name="update_working_state")
    result.expect.next_event().is_function_call_output(output="Working state saved (version 1).")
    assert not agent.resume.resumed  # on_exit released it
    assert turns(agent_memory, "room-1") == [
        ("user", "My wifi keeps dropping", ""),
        ("assistant", '{"goal": "fix the router", "focus": "checking the lights"}', "update_working_state"),
        ("tool", "Working state saved (version 1).", "update_working_state"),
        ("assistant", "Let's check the lights on your router.", ""),
    ]
    # released, so the next worker resumes at once
    assert agent_memory.client._tool("agent_release", {"agent_id": "room-1"}) == {"released": False}


async def test_a_new_worker_continues_at_once_and_writes_after_the_dead_workers_lease(agent_memory):
    sam = agent_memory.for_subject("sam")
    # The first worker resumes the call, talks, and dies without releasing.
    dead = AgentResume(agent_memory, "room-2", lease_ttl=5)
    assert (await dead.open()).fresh
    await dead.update_working_state(goal="reschedule the delivery", progress="new date is Friday")
    dead.record("user", "Can you move my delivery?")
    dead.record("assistant", "Sure, Friday works. Morning or afternoon?")
    while len(turns(agent_memory, "room-2")) < 2:  # turns are written in the background
        await asyncio.sleep(0.01)
    dead._writer.cancel()  # the worker dies: nothing more is written, and nothing is released

    started = time.monotonic()
    async with AgentSession(llm=ScriptedLLM(["Morning then."])) as session:
        agent = RecallAgent(memory=sam, instructions="Delivery line.",
                            resume=AgentResume(agent_memory, "room-2", lease_ttl=5, wait=10))
        await session.start(agent)
        await agent.wait_for_memory()
        # The briefing is there at once, while the dead worker's lease runs on.
        assert time.monotonic() - started < 2
        assert not agent.resume.lease_held
        instructions = str(agent.instructions)
        assert agent.resume.resumed and not agent.resume.context.fresh
        assert "cut off" in instructions and "do not greet the caller again" in instructions
        assert "reschedule the delivery" in instructions
        assert "Friday works. Morning or afternoon?" in instructions
        await session.run(user_input="Morning please")
        # The turns wait in the buffer until the lease is free, then land in order.
        while not agent.resume.lease_held:
            assert time.monotonic() - started < 10
            await asyncio.sleep(0.1)
        assert time.monotonic() - started >= 3
        while len(turns(agent_memory, "room-2")) < 4:
            assert time.monotonic() - started < 12
            await asyncio.sleep(0.05)

    assert [c for _, c, _ in turns(agent_memory, "room-2")][-2:] == ["Morning please", "Morning then."]


async def test_an_outage_leaves_the_call_working_without_a_briefing(agent_memory):
    sam = agent_memory.for_subject("sam")
    async with AgentSession(llm=ScriptedLLM(["Hello!"])) as session:
        agent = RecallAgent(memory=sam, instructions="Support.", resume="down-1")
        await session.start(agent)
        await agent.wait_for_memory()
        assert not agent.resume.resumed
        assert "<recall_resume>" not in str(agent.instructions)
        assert "Nothing is remembered" in str(agent.instructions)
        result = await session.run(user_input="hi")
    result.expect.next_event().is_message(role="assistant")


def test_resume_needs_an_agent_mode_client_and_a_valid_id(memory, agent_memory):
    with pytest.raises(ValueError, match="agent=True"):
        RecallAgent(memory=memory.for_subject("sam"), instructions="x", resume="room-1")
    with pytest.raises(ValueError, match="agent id"):
        RecallAgent(memory=agent_memory.for_subject("sam"), instructions="x", resume="room with spaces")
    with pytest.raises(ValueError, match="lease_ttl"):
        AgentResume(agent_memory, "room-1", lease_ttl=1)
    plain = RecallAgent(memory=agent_memory.for_subject("sam"), instructions="x")
    assert plain.resume is None
    assert [t.info.name for t in plain.tools] == ["remember"]
