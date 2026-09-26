"""The package against a real polign-server and the real ``polign mcp`` subprocess."""

import asyncio
import subprocess
import time
import uuid

import pytest
from livekit.agents.voice import AgentSession

from recall_livekit import VOICE_REGISTRY, AgentResume, RecallAgent, RecallMemory

from tests.scripted import ScriptedLLM, seen_text


def open_memory(polign, collection=None, agent=False, **kwargs):
    url, cli = polign
    return RecallMemory.open(
        command=[str(cli), "mcp", "-memory-only", "-write"] + (["-agent"] if agent else []),
        url=url,
        collection=collection or "recall_livekit_" + uuid.uuid4().hex[:8],
        agent=agent,
        predicates=VOICE_REGISTRY,
        timeout=30,
        **kwargs,
    )


async def test_voice_registry_loads_and_beliefs_survive_a_reload(polign):
    with open_memory(polign) as memory:
        assert "callback_number" in memory.registry
        assert memory.registry["open_issue"].cardinality == "multi"
        assert memory.registry["consents_to_recording"].value_type == "boolean"

        sam = memory.for_subject("caller-" + uuid.uuid4().hex[:6], read_timeout=5, write_timeout=10)
        await sam.remember("name", "Sam")
        await sam.remember("consents_to_recording", True)
        await sam.remember("open_issue", "router drops wifi")
        changed = await sam.remember("name", "Samantha")
        assert [b.value for b in changed.superseded] == ["Sam"]

        beliefs = await sam.load()
        assert {(b.predicate, b.value) for b in beliefs} == {
            ("name", "Samantha"),
            ("consents_to_recording", True),
            ("open_issue", "router drops wifi"),
        }
        hits = await sam.search("wifi router")
        assert any(b.value == "router drops wifi" for b in hits)


async def test_agent_round_trip_against_the_real_server(polign):
    with open_memory(polign) as memory:
        subject = "caller-" + uuid.uuid4().hex[:6]
        sam = memory.for_subject(subject, read_timeout=5, write_timeout=10)
        model = ScriptedLLM([("remember", {"predicate": "timezone", "value": "Denver"}), "Noted."])
        async with AgentSession(llm=model) as session:
            agent = RecallAgent(memory=sam, instructions="Support line.")
            await session.start(agent)
            await agent.wait_for_memory()
            assert "Nothing is remembered" in str(agent.instructions)
            result = await session.run(user_input="I moved to Denver")
        result.expect.next_event().is_function_call(name="remember")
        result.expect.next_event().is_function_call_output(output="Remembered timezone: Denver.")
        assert "- timezone: Denver" in seen_text(model.calls[1][0])

        # a later "call" from the same subject starts with the fact in place
        later = memory.for_subject(subject, read_timeout=5, write_timeout=10)
        async with AgentSession(llm=ScriptedLLM(["Welcome back."])) as session:
            agent = RecallAgent(memory=later, instructions="Support line.")
            await session.start(agent)
            await agent.wait_for_memory()
            assert "- timezone: Denver" in str(agent.instructions)


async def test_a_call_continues_on_a_new_worker_after_the_old_one_dies(polign):
    _, cli = polign
    usage = subprocess.run([str(cli), "mcp", "-h"], capture_output=True, text=True)
    if "-agent" not in usage.stdout + usage.stderr:
        pytest.skip(f"{cli} has no `polign mcp -agent`")
    collection = "recall_livekit_" + uuid.uuid4().hex[:8]
    room = "room-" + uuid.uuid4().hex[:6]
    caller = "caller-" + uuid.uuid4().hex[:6]

    first = open_memory(polign, collection, agent=True)
    try:
        sam = first.for_subject(caller, read_timeout=5, write_timeout=10)
        model = ScriptedLLM([
            ("update_working_state", {"goal": "move the delivery", "progress": "new date is Friday"}),
            "Friday works. Morning or afternoon?",
        ])
        session = AgentSession(llm=model)
        agent = RecallAgent(memory=sam, instructions="Delivery line.",
                            resume=AgentResume(first, room, lease_ttl=5, timeout=10))
        await session.start(agent)
        await agent.wait_for_memory()
        assert agent.resume.context.fresh
        await session.run(user_input="Can you move my delivery?")
        # Wait for the background writer, then the worker dies: its
        # subprocess is killed, so the lease is never released.
        for _ in range(100):
            if agent.resume._queue.empty():
                break
            await asyncio.sleep(0.05)
        await asyncio.sleep(0.5)
        first.client._process.kill()
        agent.resume._writer.cancel()
    finally:
        first.close()

    second = open_memory(polign, collection, agent=True)
    try:
        started = time.monotonic()
        later = second.for_subject(caller, read_timeout=5, write_timeout=10)
        async with AgentSession(llm=ScriptedLLM(["Morning then."])) as session:
            agent = RecallAgent(memory=later, instructions="Delivery line.",
                                resume=AgentResume(second, room, lease_ttl=5, wait=15, timeout=10))
            await session.start(agent)
            await agent.wait_for_memory()
            instructions = str(agent.instructions)
            assert agent.resume.resumed and not agent.resume.context.fresh
            # The briefing is ready while the dead worker's lease is still live.
            assert time.monotonic() - started < 3
            assert not agent.resume.lease_held
            for text in ("move the delivery", "Can you move my delivery?", "Friday works. Morning or afternoon?"):
                assert text in instructions
            await session.run(user_input="Morning please")
            # The new worker's turns land once the old lease runs out.
            while not agent.resume.lease_held:
                assert time.monotonic() - started < 15
                await asyncio.sleep(0.1)
            assert time.monotonic() - started >= 2
        records = second.client.resume(room).recent_turns(10)
        assert [t.content for t in records][-2:] == ["Morning please", "Morning then."]
    finally:
        second.close()
