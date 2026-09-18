"""The package against a real polign-server and the real ``polign mcp`` subprocess."""

import uuid

from livekit.agents.voice import AgentSession

from recall_livekit import VOICE_REGISTRY, RecallAgent, RecallMemory

from tests.scripted import ScriptedLLM, seen_text


def open_memory(polign, **kwargs):
    url, cli = polign
    return RecallMemory.open(
        command=[str(cli), "mcp", "-memory-only", "-write"],
        url=url,
        collection="recall_livekit_" + uuid.uuid4().hex[:8],
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
