"""Runs RecallAgent through a real AgentSession in text mode with a scripted model."""

import pytest
from livekit.agents import llm
from livekit.agents.llm.chat_context import Instructions
from livekit.agents.voice import Agent, AgentSession

from recall_livekit import RecallAgent, attach

from tests.scripted import ScriptedLLM, seen_text


async def test_memory_block_is_in_the_first_call_and_refreshes_after_remember(memory):
    sam = memory.for_subject("sam")
    await sam.remember("name", "Sam")
    model = ScriptedLLM(
        [
            ("remember", {"predicate": "timezone", "value": "Denver"}),
            "Got it, Denver it is.",
        ]
    )
    async with AgentSession(llm=model) as session:
        agent = RecallAgent(memory=sam, instructions="You are the Acme support line.")
        await session.start(agent)
        await agent.wait_for_memory()

        assert "- name: Sam" in str(agent.instructions)
        assert agent.instructions.startswith("You are the Acme support line.")

        result = await session.run(user_input="I moved to Denver by the way")

    result.expect.next_event().is_function_call(name="remember", arguments={"predicate": "timezone", "value": "Denver"})
    result.expect.next_event().is_function_call_output(output="Remembered timezone: Denver.")
    result.expect.next_event().is_message(role="assistant")
    result.expect.no_more_events()

    first_ctx, first_tools = model.calls[0]
    assert "- name: Sam" in seen_text(first_ctx)
    assert "- timezone" not in seen_text(first_ctx)
    assert [t.info.name for t in first_tools] == ["remember"]

    # the follow-up call, after the tool ran, already carries the new fact
    second_ctx, _ = model.calls[1]
    assert "- timezone: Denver" in seen_text(second_ctx)
    assert "- timezone: Denver" in str(agent.instructions)
    assert str(agent.instructions).count("<recall_memory>") == 1


async def test_remember_reports_supersession_and_rejects_bad_input(memory):
    sam = memory.for_subject("sam")
    await sam.remember("name", "Sam")
    model = ScriptedLLM(
        [
            ("remember", {"predicate": "name", "value": "Samantha"}),
            ("remember", {"predicate": "age", "value": "forty"}),
            ("remember", {"predicate": "favourite_colour", "value": "blue"}),
            "Done.",
        ]
    )
    async with AgentSession(llm=model) as session:
        await session.start(RecallAgent(memory=sam, instructions="Support."))
        result = await session.run(user_input="Call me Samantha")

    result.expect.next_event().is_function_call(name="remember")
    result.expect.next_event().is_function_call_output(output="Updated name to Samantha; it was Sam.")
    result.expect.next_event().is_function_call(name="remember")
    out = result.expect.next_event().is_function_call_output().event().item
    assert out.is_error and "takes a number" in (out.output or "")
    result.expect.next_event().is_function_call(name="remember")
    out = result.expect.next_event().is_function_call_output().event().item
    assert out.is_error and "Unknown memory kind" in (out.output or "")
    result.expect.next_event().is_message(role="assistant")


async def test_forget_tool_is_opt_in(memory):
    sam = memory.for_subject("sam")
    await sam.remember("open_issue", "wifi drops")
    model = ScriptedLLM([("forget", {"predicate": "open_issue", "value": "wifi drops"}), "Forgotten."])
    async with AgentSession(llm=model) as session:
        agent = RecallAgent(memory=sam, instructions="Support.", forget_tool=True)
        await session.start(agent)
        await agent.wait_for_memory()
        assert sorted(t.info.name for t in agent.tools) == ["forget", "remember"]
        result = await session.run(user_input="Please forget the wifi thing")

    result.expect.next_event().is_function_call(name="forget")
    result.expect.next_event().is_function_call_output(output="Withdrew 1 remembered value(s) of open_issue.")
    assert await sam.load() == []

    plain = RecallAgent(memory=sam, instructions="Support.", remember_tool=False)
    assert plain.tools == []


async def test_overflow_adds_a_search_result_to_the_turn(memory):
    # A text-mode run skips on_user_turn_completed (it fires at a real end of
    # turn), so the hook is driven directly with the turn context LiveKit hands it.
    sam = memory.for_subject("sam", limit=2)
    for issue in ("router drops wifi", "billing double charge", "app crashes on login"):
        await sam.remember("open_issue", issue)
    async with AgentSession(llm=ScriptedLLM(["Let me look."])) as session:
        agent = RecallAgent(memory=sam, instructions="Support.")
        await session.start(agent)
        await agent.wait_for_memory()
        assert sam.overflowed and "app crashes on login" not in str(agent.instructions)

        turn_ctx = agent.chat_ctx.copy()
        message = llm.ChatMessage(role="user", content=["the login crash is back"])
        await agent.on_user_turn_completed(turn_ctx, message)
        added = turn_ctx.items[-1]
        assert added.type == "message" and added.role == "system"
        assert "app crashes on login" in (added.text_content or "")
        assert "remember tool" not in (added.text_content or "")

        # a hit already shown in the instructions adds nothing
        turn_ctx = agent.chat_ctx.copy()
        before = len(turn_ctx.items)
        await agent.on_user_turn_completed(turn_ctx, llm.ChatMessage(role="user", content=["wifi router again"]))
        assert len(turn_ctx.items) == before

        quiet = RecallAgent(memory=sam, instructions="Support.", search_when_overflowed=False)
        turn_ctx = agent.chat_ctx.copy()
        before = len(turn_ctx.items)
        await quiet.on_user_turn_completed(turn_ctx, message)
        assert len(turn_ctx.items) == before


async def test_memory_outage_leaves_the_agent_working(memory):
    broken = memory.for_subject("broken")
    model = ScriptedLLM([("remember", {"predicate": "name", "value": "Sam"}), "Sorry, noted anyway."])
    async with AgentSession(llm=model) as session:
        agent = RecallAgent(memory=broken, instructions="Support.")
        await session.start(agent)
        await agent.wait_for_memory()
        assert "Nothing is remembered about the caller yet." in str(agent.instructions)
        result = await session.run(user_input="I'm Sam")

    result.expect.next_event().is_function_call(name="remember")
    out = result.expect.next_event().is_function_call_output().event().item
    assert out.is_error and "not saved" in (out.output or "")
    result.expect.next_event().is_message(role="assistant")


async def test_attach_to_an_existing_agent(memory):
    sam = memory.for_subject("sam")
    await sam.remember("name", "Sam")

    class Existing(Agent):
        def __init__(self) -> None:
            super().__init__(instructions="You are a hotel front desk.")

    agent = Existing()
    binding = await attach(agent, sam, who="the guest")
    assert "Facts remembered about the guest" in str(agent.instructions)
    assert [t.info.name for t in agent.tools] == ["remember"]

    model = ScriptedLLM([("remember", {"predicate": "timezone", "value": "Lisbon"}), "Noted."])
    async with AgentSession(llm=model) as session:
        await session.start(agent)
        await session.run(user_input="I'm calling from Lisbon")
    assert "- timezone: Lisbon" in str(agent.instructions)
    assert binding.memory is sam

    with pytest.raises(TypeError):
        await attach(Agent(instructions=Instructions("x", audio="short")), sam)


def test_subclass_agents_can_add_their_own_tools(memory):
    sam = memory.for_subject("sam")

    @llm.function_tool
    async def lookup_order(order_id: str) -> str:
        """Look up an order."""
        return order_id

    agent = RecallAgent(memory=sam, instructions="Support.", tools=[lookup_order])
    assert sorted(t.info.name for t in agent.tools) == ["lookup_order", "remember"]
    assert agent.base_instructions == "Support."
    with pytest.raises(TypeError):
        RecallAgent(memory=sam, instructions=Instructions("x"))
