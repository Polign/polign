from __future__ import annotations

import json
import threading
import time

import pytest

from .fake_client import Earlier, FakeClient, Memory, RecallError, TextResult

SEATTLE = Memory("Lives in Portland", "2026-10-20", 13, (Earlier("Lives in Seattle", "2026-09-01"),))
VIM = Memory("Edits in vim", "2026-10-01", 32)


@pytest.fixture
def made(plugin, hermes_home, secrets):
    """A provider over a FakeClient, initialized for the test profile with an extraction model."""
    secrets["ANTHROPIC_API_KEY"] = "sk-ant"
    client = FakeClient(memories=[SEATTLE, VIM])
    provider = plugin.RecallMemoryProvider(client_factory=lambda settings: client)
    provider.initialize("session-1", hermes_home=str(hermes_home), platform="cli", agent_context="primary")
    assert provider.idle(5)
    yield provider, client
    provider.shutdown()


def test_registers_a_new_instance_per_load(plugin):
    seen = []

    class Ctx:
        def register_memory_provider(self, provider):
            seen.append(provider)

    plugin.register(Ctx())
    plugin.register(Ctx())
    assert len(seen) == 2 and seen[0] is not seen[1]
    assert seen[0].name == "polign-recall"


def test_settings_default_to_a_local_database_under_the_profile(plugin, hermes_home, secrets):
    secrets["ANTHROPIC_API_KEY"] = "sk-ant"
    s = plugin.resolve_settings(str(hermes_home), lambda k: secrets.get(k, ""))
    assert s["backend"] == "local"
    assert s["backend_url"] == str(hermes_home / "recall")
    assert s["url"] == ""
    assert s["extract_model"] == "anthropic:claude-haiku-5-5"
    assert s["extract_key_name"] == "ANTHROPIC_API_KEY" and s["extract_key"] == "sk-ant"


def test_settings_follow_the_environment_then_the_file(plugin, hermes_home, secrets):
    (hermes_home / "recall.json").write_text(json.dumps({"url": "http://file:8080/", "extract_model": "ollama:llama3.2"}))
    s = plugin.resolve_settings(str(hermes_home), lambda k: secrets.get(k, ""))
    assert s["url"] == "http://file:8080" and s["extract_model"] == "ollama:llama3.2" and s["extract_key_name"] == ""
    secrets.update({"RECALL_URL": "http://env:8080", "RECALL_API_KEY": "rcl_x", "RECALL_EXTRACT_MODEL": "none",
                    "OPENAI_API_KEY": "sk-o"})
    s = plugin.resolve_settings(str(hermes_home), lambda k: secrets.get(k, ""))
    assert s["url"] == "http://env:8080" and s["api_key"] == "rcl_x"
    assert s["extract_model"] == "", "'none' means keep text as written, even with a key in .env"


def test_settings_keep_a_chosen_database(plugin, hermes_home, secrets):
    secrets.update({"RECALL_BACKEND": "pgvector", "RECALL_BACKEND_URL": "postgres://u@h/db", "RECALL_BACKEND_KEY": "pw"})
    s = plugin.resolve_settings(str(hermes_home), lambda k: secrets.get(k, ""))
    assert (s["backend"], s["backend_url"], s["backend_key"]) == ("pgvector", "postgres://u@h/db", "pw")


def test_open_client_passes_the_settings_through(plugin, monkeypatch):
    seen = {}

    class Client:
        def __init__(self, **kwargs):
            seen.update(kwargs)

    monkeypatch.setitem(__import__("sys").modules, "polign_recall", type("m", (), {"Client": Client}))
    plugin.open_client({"url": "", "api_key": "", "token_file": "", "backend": "local", "backend_url": "/x",
                        "backend_key": "", "extract_model": "anthropic:claude-haiku-5-5",
                        "extract_key_name": "ANTHROPIC_API_KEY", "extract_key": "sk"})
    assert seen["text"] is True and seen["backend"] == "local" and seen["backend_url"] == "/x"
    assert seen["extract_model"] == "anthropic:claude-haiku-5-5" and seen["env"] == {"ANTHROPIC_API_KEY": "sk"}
    assert seen["inherit_env"] is False, "only the extraction key leaves the Hermes process"
    seen.clear()
    plugin.open_client({"url": "http://s", "api_key": "rcl", "token_file": "", "backend": "", "backend_url": "",
                        "backend_key": "", "extract_model": "", "extract_key_name": "", "extract_key": ""})
    assert seen == {"url": "http://s", "api_key": "rcl", "token_file": None, "timeout": 120.0}


def test_availability(plugin, secrets, monkeypatch):
    provider = plugin.RecallMemoryProvider()
    monkeypatch.setitem(__import__("sys").modules, "polign_recall", type("m", (), {}))
    assert provider.is_available()
    secrets["RECALL_URL"] = "http://s"
    assert not provider.is_available() and "RECALL_API_KEY" in provider.unavailable_reason()
    secrets["RECALL_API_KEY"] = "rcl"
    assert provider.is_available()
    monkeypatch.delitem(__import__("sys").modules, "polign_recall")
    monkeypatch.setattr("builtins.__import__", _refuse("polign_recall"))
    assert not provider.is_available() and "polign-recall" in provider.unavailable_reason()


def _refuse(name):
    real = __import__

    def fake(n, *a, **k):
        if n == name:
            raise ImportError(name)
        return real(n, *a, **k)
    return fake


def test_save_config_writes_the_profile_file(plugin, hermes_home):
    provider = plugin.RecallMemoryProvider()
    provider.save_config({"url": "http://s/", "extract_model": "", "api_key": "never"}, str(hermes_home))
    data = json.loads((hermes_home / "recall.json").read_text())
    assert data == {"url": "http://s/"}
    provider.save_config({"url": "", "extract_model": "none"}, str(hermes_home))
    assert json.loads((hermes_home / "recall.json").read_text()) == {"extract_model": "none"}
    assert "none" in provider.get_status_config({})["extract_model"]


def test_prefetch_puts_memories_in_front_of_the_turn(made):
    provider, client = made
    provider.on_turn_start(1, "where do I live?")
    block = provider.prefetch("where do I live?")
    assert block.startswith("## Recall Memory")
    assert '- Lives in Portland (stated 2026-10-20; replaced "Lives in Seattle" from 2026-09-01)' in block
    assert "vim" not in block
    status = provider.recall_status()
    assert status.provider_label == "Recall" and status.count == 1
    assert provider.prefetch("where do I live?") == "", "the same turn's later API calls add nothing"
    assert provider.recall_status() is None
    assert [c for c in client.calls if c[0] == "ask"] == [("ask", {"question": "where do I live?", "as_of": None, "since": None})]


def test_prefetch_skips_trivial_prompts_and_empty_answers(made):
    provider, client = made
    assert provider.prefetch("ok") == ""
    assert provider.prefetch("anything about rockets?") == ""
    assert provider.recall_status() is None
    assert all(c[1].get("question") != "ok" for c in client.calls)


def test_prefetch_does_not_wait_past_its_budget(plugin, made, monkeypatch):
    provider, client = made
    monkeypatch.setattr(plugin, "PREFETCH_WAIT_SECS", 0.2)
    client.gate = threading.Event()
    started = time.monotonic()
    assert provider.prefetch("where do I live?") == ""
    assert time.monotonic() - started < 1.0
    client.gate.set()


def test_sync_turn_sends_the_turn_in_the_background(made):
    provider, client = made
    provider.sync_turn("I moved to Portland last week.", "Noted, Portland it is.", session_id="session-1",
                       messages=[{"role": "user", "content": "x"}], turn_author={"id": "u"})
    assert provider.idle(5)
    name, args = client.calls[-1]
    assert name == "remember"
    assert args["messages"] == [{"role": "user", "content": "I moved to Portland last week."},
                                {"role": "assistant", "content": "Noted, Portland it is."}]
    assert client.memories[-1].text == "I moved to Portland last week."


def test_sync_turn_truncates_long_turns_at_a_sentence_end(plugin, made):
    provider, client = made
    long = ("A sentence that goes on. " * 400).strip()
    provider.sync_turn(long, "", session_id="s")
    assert provider.idle(5)
    sent = client.calls[-1][1]["messages"]
    assert len(sent) == 1 and len(sent[0]["content"]) <= plugin.SYNC_MSG_MAX_CHARS and sent[0]["content"].endswith(".")


def test_automatic_writes_skip_non_primary_contexts(plugin, hermes_home):
    client = FakeClient()
    provider = plugin.RecallMemoryProvider(client_factory=lambda s: client)
    provider.initialize("s", hermes_home=str(hermes_home), agent_context="subagent")
    provider.sync_turn("I like tea.", "ok", session_id="s")
    provider.on_memory_write("add", "memory", "Prefers tea")
    assert provider.idle(5)
    assert [c for c in client.calls if c[0] != "connect"] == []
    out = json.loads(provider.handle_tool_call("polign_recall_remember", {"text": "I like tea."}))
    assert out["remembered"][0]["text"] == "I like tea.", "the agent's own tools still work"
    provider.shutdown()


def test_memory_writes_are_mirrored(made):
    provider, client = made
    provider.on_memory_write("add", "memory", "Prefers short answers", {"write_origin": "tool"})
    provider.on_memory_write("replace", "user", "Lives in Bend", {"previous_content": "Lives in Portland"})
    provider.on_memory_write("remove", "memory", "", {"previous_content": "Edits in vim"})
    assert provider.idle(5)
    names = [c[0] for c in client.calls if c[0] in ("remember", "forget")]
    assert names == ["remember", "remember", "forget"]
    assert client.calls[-1][1] == {"text": "Edits in vim"}
    assert sorted(m.text for m in client.memories) == ["Lives in Bend", "Prefers short answers"]


def test_remove_is_not_mirrored_without_an_extraction_model(plugin, hermes_home):
    client = FakeClient()
    provider = plugin.RecallMemoryProvider(client_factory=lambda s: client)
    provider.initialize("s", hermes_home=str(hermes_home))
    provider._settings["extract_model"] = ""
    provider.on_memory_write("remove", "memory", "", {"previous_content": "Edits in vim"})
    assert provider.idle(5)
    assert not [c for c in client.calls if c[0] == "forget"]
    provider.shutdown()


def test_tools(made):
    provider, client = made
    names = [s["name"] for s in provider.get_tool_schemas()]
    assert names == ["polign_recall_search", "polign_recall_profile", "polign_recall_remember", "polign_recall_forget", "polign_recall_history"]
    assert all(s["parameters"]["type"] == "object" for s in provider.get_tool_schemas())

    out = json.loads(provider.handle_tool_call("polign_recall_search", {"question": "where does the user live", "as_of": "2026-10-01T00:00:00Z"}))
    assert out == {"memories": [{"text": "Lives in Portland", "since": "2026-10-20", "days_ago": 13,
                                 "before": [{"text": "Lives in Seattle", "since": "2026-09-01"}]}]}
    assert client.calls[-1] == ("ask", {"question": "where does the user live", "as_of": "2026-10-01T00:00:00Z", "since": None})

    out = json.loads(provider.handle_tool_call("polign_recall_profile", {}))
    assert [m["text"] for m in out["memories"]] == ["Lives in Portland", "Edits in vim"]

    out = json.loads(provider.handle_tool_call("polign_recall_remember", {"text": "Lives in Bend", "observed_at": "2026-11-01T00:00:00Z"}))
    assert out == {"remembered": [{"text": "Lives in Bend", "replaces": ["Lives in Portland"]}]}
    assert client.calls[-1][1]["observed_at"] == "2026-11-01T00:00:00Z"

    out = json.loads(provider.handle_tool_call("polign_recall_history", {"question": "where I live"}))
    assert out["history"][0]["about"] == "where I live"
    assert out["history"][0]["changes"][1] == {"when": "2026-11-02T00:00:00Z", "change": "replaced",
                                               "ended": ["Lives in Seattle"], "began": ["Lives in Portland"]}

    out = json.loads(provider.handle_tool_call("polign_recall_forget", {"text": "vim"}))
    assert out == {"forgotten": ["Edits in vim"]}


def test_tool_errors_are_json(made):
    provider, client = made
    assert json.loads(provider.handle_tool_call("polign_recall_search", {}))["error"] == "question is required"
    assert "Unknown tool" in json.loads(provider.handle_tool_call("nope", {}))["error"]
    client.fail = RecallError("no model on the server", code="unsupported")
    assert json.loads(provider.handle_tool_call("polign_recall_forget", {"text": "x"}))["error"] == "unsupported: no model on the server"


def test_kept_as_text_is_explained(plugin, made):
    provider, client = made
    client.remember = lambda **kw: TextResult((), True)
    out = json.loads(provider.handle_tool_call("polign_recall_remember", {"text": "hello"}))
    assert out["kept_as_text"] is True and "extraction model" in out["note"]


def test_breaker_opens_after_repeated_failures_and_a_dead_transport_reopens(plugin, hermes_home, monkeypatch):
    clients = []

    def factory(settings):
        c = FakeClient(fail=RecallError("gone", code="transport_error"))
        clients.append(c)
        return c

    provider = plugin.RecallMemoryProvider(client_factory=factory)
    provider.initialize("s", hermes_home=str(hermes_home))
    assert provider.idle(5)
    for _ in range(plugin.BREAKER_THRESHOLD):
        assert "error" in json.loads(provider.handle_tool_call("polign_recall_profile", {}))
    assert len(clients) == plugin.BREAKER_THRESHOLD, "a dead transport is closed and reopened on the next call"
    assert all(c.closed for c in clients)
    assert provider._breaker_open_until > time.monotonic()
    out = json.loads(provider.handle_tool_call("polign_recall_profile", {}))
    assert "unavailable" in out["error"] and len(clients) == plugin.BREAKER_THRESHOLD
    assert provider.prefetch("where do I live?") == ""
    provider._breaker_open_until = 0.0
    clients[-1].fail = None
    json.loads(provider.handle_tool_call("polign_recall_profile", {}))
    assert len(clients) == plugin.BREAKER_THRESHOLD + 1
    provider.shutdown()


def test_shutdown_drains_writes_and_closes(made):
    provider, client = made
    provider.sync_turn("I like tea.", "", session_id="s")
    provider.shutdown()
    assert client.closed
    assert ("remember", {"text": None, "messages": [{"role": "user", "content": "I like tea."}], "observed_at": None}) in client.calls
    provider.sync_turn("after shutdown", "", session_id="s")
    assert provider._writes.qsize() == 0


def test_system_prompt_names_the_tools(made):
    provider, _ = made
    block = provider.system_prompt_block()
    assert block.startswith("# Recall Memory") and "polign_recall_search" in block and "polign_recall_remember" in block
