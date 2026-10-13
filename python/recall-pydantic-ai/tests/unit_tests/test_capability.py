from dataclasses import dataclass

import pytest
from pydantic_ai import Agent

from recall_pydantic_ai import RecallMemory, coerce

from ..scripted import Scripted


def make_agent(script: Scripted, memory: RecallMemory, **kwargs) -> Agent:
    return Agent(script.model(), instructions="You help with the project.", capabilities=[memory], **kwargs)


def test_remember_then_next_run_sees_the_fact(client):
    script = Scripted(calls=[("remember", {"predicate": "name", "value": "Sam"})])
    agent = make_agent(script, RecallMemory(client, "u1"))

    result = agent.run_sync("call me Sam")
    assert result.output == "Remembered name: Sam."
    assert "Nothing is remembered about the user yet." in script.seen[0].instructions
    assert script.seen[0].instructions.startswith("You help with the project.")
    assert script.seen[0].tools == ["remember"]

    agent.run_sync("hello")
    assert "- name: Sam (observed 2026-10-13)" in script.seen[-1].instructions


def test_correction_replaces_the_old_value(client):
    client.remember("u1", "name", "Alex")
    script = Scripted(calls=[("remember", {"predicate": "name", "value": "Sam"})])
    agent = make_agent(script, RecallMemory(client, "u1"))

    assert agent.run_sync("actually call me Sam").output == "Updated name to Sam; it was Alex."
    assert "- name: Alex" in script.seen[0].instructions

    agent.run_sync("hi")
    assert "- name: Sam" in script.seen[-1].instructions
    assert "Alex" not in script.seen[-1].instructions


def test_already_known(client):
    client.remember("u1", "timezone", "America/Denver")
    script = Scripted(calls=[("remember", {"predicate": "timezone", "value": "America/Denver"})])
    out = make_agent(script, RecallMemory(client, "u1")).run_sync("I'm in Denver").output
    assert out == "Already remembered: timezone is America/Denver."


def test_multi_valued_facts_group_on_one_line(client):
    client.remember("u1", "uses_technology", "Go")
    client.remember("u1", "uses_technology", "Postgres")
    script = Scripted()
    make_agent(script, RecallMemory(client, "u1")).run_sync("hi")
    assert "- uses_technology: Go; Postgres" in script.seen[0].instructions


@dataclass
class Deps:
    user_id: str


def test_subject_from_deps_keeps_users_apart(client):
    client.remember("alice", "name", "Alice")
    client.remember("bob", "name", "Bob")
    script = Scripted()
    agent = Agent(script.model(), deps_type=Deps, capabilities=[RecallMemory(client, lambda ctx: ctx.deps.user_id)])

    agent.run_sync("hi", deps=Deps("alice"))
    agent.run_sync("hi", deps=Deps("bob"))
    assert "Alice" in script.seen[0].instructions and "Bob" not in script.seen[0].instructions
    assert "Bob" in script.seen[1].instructions and "Alice" not in script.seen[1].instructions


def test_writes_go_to_the_run_subject(client):
    script = Scripted()
    agent = Agent(script.model(), deps_type=Deps, capabilities=[RecallMemory(client, lambda ctx: ctx.deps.user_id)])
    script.calls = [("remember", {"predicate": "name", "value": "Bo"})]
    agent.run_sync("I'm Bo", deps=Deps("bob"))
    assert [b.value for b in client.beliefs["bob"]] == ["Bo"]
    assert "alice" not in client.beliefs


def test_empty_subject_is_refused(client):
    with pytest.raises(ValueError):
        RecallMemory(client, " ")
    agent = Agent(Scripted().model(), deps_type=Deps, capabilities=[RecallMemory(client, lambda ctx: ctx.deps.user_id)])
    with pytest.raises(ValueError, match="non-empty"):
        agent.run_sync("hi", deps=Deps(""))


def test_read_failure_fails_open(client, caplog):
    client.remember("u1", "name", "Sam")
    client.fail_reads = True
    script = Scripted()
    assert make_agent(script, RecallMemory(client, "u1")).run_sync("hi").output == "done"
    assert "Nothing is remembered" in script.seen[0].instructions
    assert "recall load failed" in caplog.text


def test_write_failure_is_told_to_the_model(client):
    client.fail_writes = True
    script = Scripted(calls=[("remember", {"predicate": "name", "value": "Sam"})])
    out = make_agent(script, RecallMemory(client, "u1")).run_sync("call me Sam").output
    assert "The fact was not saved: recall is down" in out


def test_unknown_predicate_asks_the_model_to_retry(client):
    script = Scripted(calls=[("remember", {"predicate": "shoe_size", "value": "10"})])
    out = make_agent(script, RecallMemory(client, "u1")).run_sync("size 10").output
    assert "Unknown memory kind 'shoe_size'" in out
    assert not any(c[0] == "remember" for c in client.calls)


def test_remember_schema_lists_the_registry(client):
    memory = RecallMemory(client, "u1")
    agent = make_agent(Scripted(), memory)
    captured = {}

    def model(messages, info):
        captured.update({t.name: t for t in info.function_tools})
        from pydantic_ai.messages import ModelResponse, TextPart
        return ModelResponse(parts=[TextPart("ok")])

    from pydantic_ai.models.function import FunctionModel
    agent.run_sync("hi", model=FunctionModel(model))
    schema = captured["remember"].parameters_json_schema
    assert schema["properties"]["predicate"]["enum"] == ["name", "timezone", "team_size", "on_call", "uses_technology"]
    assert "(can hold several values)" in schema["properties"]["predicate"]["description"]


def test_typed_values_are_coerced(client):
    script = Scripted(calls=[
        ("remember", {"predicate": "team_size", "value": "1,200"}),
        ("remember", {"predicate": "on_call", "value": "yes"}),
    ])
    make_agent(script, RecallMemory(client, "u1")).run_sync("we are 1,200 and I'm on call")
    stored = {b.predicate: b.value for b in client.beliefs["u1"]}
    assert stored == {"team_size": 1200, "on_call": True}


def test_coerce_rejects_bad_values(client):
    registry = RecallMemory(client, "u1").registry
    with pytest.raises(ValueError, match="takes a number"):
        coerce(registry, "team_size", "lots")
    with pytest.raises(ValueError, match="takes yes or no"):
        coerce(registry, "on_call", "maybe")
    with pytest.raises(ValueError, match="needs a value"):
        coerce(registry, "name", "  ")
    assert coerce(registry, "team_size", "2.5") == 2.5


def test_forget_is_off_by_default_and_works_when_on(client):
    client.remember("u1", "timezone", "UTC")
    script = Scripted()
    make_agent(script, RecallMemory(client, "u1")).run_sync("hi")
    assert "forget" not in script.seen[0].tools

    script = Scripted(calls=[("forget", {"predicate": "timezone", "everything": True})])
    out = make_agent(script, RecallMemory(client, "u1", forget_tool=True)).run_sync("forget my timezone").output
    assert out == "Withdrew 1 remembered value(s) of timezone."
    assert client.beliefs["u1"] == []


def test_forget_needs_a_value_or_everything(client):
    script = Scripted(calls=[("forget", {"predicate": "timezone"})])
    out = make_agent(script, RecallMemory(client, "u1", forget_tool=True)).run_sync("forget it").output
    assert "Give the value to withdraw" in out


def test_overflow_searches_for_the_prompt_and_offers_search_tool(client):
    client.remember("u1", "name", "Sam")
    client.remember("u1", "timezone", "America/Denver")
    client.remember("u1", "uses_technology", "Postgres")
    script = Scripted()
    make_agent(script, RecallMemory(client, "u1", limit=2)).run_sync("which database, postgres?")

    seen = script.seen[0]
    assert "search_memory" in seen.tools
    assert "match this request:\n- uses_technology: Postgres" in seen.instructions
    assert ("recall", "u1", "which database, postgres?", 5) in client.calls


def test_search_tool(client):
    for tech in ("Go", "Postgres", "Redis"):
        client.remember("u1", "uses_technology", tech)
    script = Scripted(calls=[("search_memory", {"query": "redis"})])
    out = make_agent(script, RecallMemory(client, "u1", limit=1, search_when_overflowed=False)).run_sync("hi").output
    assert out == "- uses_technology: Redis"


def test_no_search_tool_when_everything_fits(client):
    script = Scripted()
    make_agent(script, RecallMemory(client, "u1")).run_sync("hi")
    assert script.seen[0].tools == ["remember"]


def test_read_only(client):
    client.remember("u1", "name", "Sam")
    script = Scripted()
    make_agent(script, RecallMemory(client, "u1", remember_tool=False, who="the customer")).run_sync("hi")
    assert script.seen[0].tools == []
    assert "Facts remembered about the customer" in script.seen[0].instructions
    assert "remember tool" not in script.seen[0].instructions


def test_custom_template(client):
    script = Scripted()
    make_agent(script, RecallMemory(client, "u1", template="## Memory\n{context}")).run_sync("hi")
    assert "## Memory\nNothing is remembered" in script.seen[0].instructions
    with pytest.raises(ValueError, match="placeholder"):
        RecallMemory(client, "u1", template="no placeholder")


async def test_concurrent_runs_do_not_share_state(client):
    import asyncio

    client.remember("alice", "name", "Alice")
    client.remember("bob", "name", "Bob")
    script = Scripted()
    agent = Agent(script.model(), deps_type=Deps, capabilities=[RecallMemory(client, lambda ctx: ctx.deps.user_id)])
    await asyncio.gather(*(agent.run("hi", deps=Deps(u)) for u in ["alice", "bob"] * 5))
    for seen in script.seen:
        assert ("Alice" in seen.instructions) != ("Bob" in seen.instructions)
