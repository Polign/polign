"""The capability over a client serving Recall's text tools."""

import pytest
from pydantic_ai import Agent

from recall_pydantic_ai import RecallMemory, is_text

from ..scripted import Scripted


def make_agent(script: Scripted, memory: RecallMemory) -> Agent:
    return Agent(script.model(), instructions="You help with the project.", capabilities=[memory])


def test_text_client_is_detected(client, text_client):
    assert is_text(text_client) and not is_text(client)
    memory = RecallMemory(text_client)
    assert memory.text and memory.registry == {}


def test_text_client_refuses_a_subject(text_client):
    with pytest.raises(ValueError, match="take no subject"):
        RecallMemory(text_client, "u1")


def test_typed_client_needs_a_subject(client):
    with pytest.raises(ValueError, match="need a subject"):
        RecallMemory(client)


def test_remember_then_next_run_sees_it(text_client):
    script = Scripted(calls=[("remember", {"text": "the user's name is Sam"})])
    agent = make_agent(script, RecallMemory(text_client))

    assert agent.run_sync("call me Sam").output == "Remembered: the user's name is Sam."
    first = script.seen[0]
    assert "Nothing is remembered about the user yet." in first.instructions
    assert "call the remember tool with it, in their words" in first.instructions
    assert first.tools == ["remember"]

    agent.run_sync("hello")
    assert "- the user's name is Sam (since 2026-10-21)" in script.seen[-1].instructions


def test_remember_schema_takes_text(text_client):
    captured = {}

    def model(messages, info):
        captured.update({t.name: t for t in info.function_tools})
        from pydantic_ai.messages import ModelResponse, TextPart
        return ModelResponse(parts=[TextPart("ok")])

    from pydantic_ai.models.function import FunctionModel
    make_agent(Scripted(), RecallMemory(text_client)).run_sync("hi", model=FunctionModel(model))
    schema = captured["remember"].parameters_json_schema
    assert list(schema["properties"]) == ["text"] and schema["required"] == ["text"]


def test_correction_says_what_it_replaced(text_client):
    text_client.remember(text="the user's editor is Vim")
    script = Scripted(calls=[("remember", {"text": "the user's editor is Helix"})])
    agent = make_agent(script, RecallMemory(text_client))

    out = agent.run_sync("I switched to Helix").output
    assert out == "Updated: the user's editor is Helix; it replaced the user's editor is Vim."
    assert "- the user's editor is Vim" in script.seen[0].instructions

    agent.run_sync("hi")
    assert "Helix" in script.seen[-1].instructions and "Vim" not in script.seen[-1].instructions


def test_already_known(text_client):
    text_client.remember(text="the user lives in Denver")
    script = Scripted(calls=[("remember", {"text": "the user lives in Denver"})])
    out = make_agent(script, RecallMemory(text_client)).run_sync("I'm in Denver").output
    assert out == "Already remembered: the user lives in Denver."


def test_server_without_extraction_keeps_text(text_client):
    text_client.kept_only = True
    script = Scripted(calls=[("remember", {"text": "I like tea"})])
    assert make_agent(script, RecallMemory(text_client)).run_sync("I like tea").output == "Kept as written."


def test_empty_text_asks_the_model_to_retry(text_client):
    script = Scripted(calls=[("remember", {"text": "  "})])
    out = make_agent(script, RecallMemory(text_client)).run_sync("hi").output
    assert "Pass what to remember as text." in out
    assert not any(c[0] == "remember" for c in text_client.calls)


def test_read_failure_fails_open(text_client, caplog):
    text_client.remember(text="the user's name is Sam")
    text_client.fail_reads = True
    script = Scripted()
    assert make_agent(script, RecallMemory(text_client)).run_sync("hi").output == "done"
    assert "Nothing is remembered" in script.seen[0].instructions
    assert "recall load failed" in caplog.text


def test_write_failure_is_told_to_the_model(text_client):
    text_client.fail_writes = True
    script = Scripted(calls=[("remember", {"text": "the user's name is Sam"})])
    out = make_agent(script, RecallMemory(text_client)).run_sync("call me Sam").output
    assert "Nothing was saved: recall is down" in out


def test_forget_is_off_by_default_and_works_when_on(text_client):
    text_client.remember(text="the user's editor is Vim")
    script = Scripted()
    make_agent(script, RecallMemory(text_client)).run_sync("hi")
    assert "forget" not in script.seen[0].tools

    script = Scripted(calls=[("forget", {"text": "my editor"})])
    out = make_agent(script, RecallMemory(text_client, forget_tool=True)).run_sync("forget my editor").output
    assert out == "Withdrew: the user's editor is Vim."
    assert text_client.memories == []

    script = Scripted(calls=[("forget", {"text": "my shoe size"})])
    out = make_agent(script, RecallMemory(text_client, forget_tool=True)).run_sync("forget it").output
    assert out == "Nothing remembered matched, so nothing was withdrawn."


def test_overflow_asks_with_the_prompt_and_offers_search(text_client):
    for text in ("the user's name is Sam", "the user lives in Denver", "the project database is Postgres"):
        text_client.remember(text=text)
    script = Scripted()
    make_agent(script, RecallMemory(text_client, limit=2)).run_sync("which database?")

    seen = script.seen[0]
    assert "search_memory" in seen.tools
    assert "match this request:\n- the project database is Postgres" in seen.instructions
    assert ("ask", "which database?") in text_client.calls


def test_no_search_tool_when_everything_fits(text_client):
    text_client.remember(text="the user's name is Sam")
    script = Scripted()
    make_agent(script, RecallMemory(text_client, limit=1)).run_sync("hi")
    assert script.seen[0].tools == ["remember"]


def test_search_tool(text_client):
    for text in ("the user's name is Sam", "the cache is Redis"):
        text_client.remember(text=text)
    script = Scripted(calls=[("search_memory", {"query": "redis"})])
    memory = RecallMemory(text_client, limit=1, search_when_overflowed=False)
    assert make_agent(script, memory).run_sync("hi").output == "- the cache is Redis (since 2026-10-21)"


def test_read_only(text_client):
    text_client.remember(text="the customer's name is Sam")
    script = Scripted()
    make_agent(script, RecallMemory(text_client, remember_tool=False, who="the customer")).run_sync("hi")
    assert script.seen[0].tools == []
    assert "Facts remembered about the customer" in script.seen[0].instructions
    assert "remember tool" not in script.seen[0].instructions
