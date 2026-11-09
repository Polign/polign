"""The provider over the real `recall` binary that polign-recall installs, with a local database
under a temporary profile and no extraction model: text is kept as written and found by its words."""

from __future__ import annotations

import json
import os
import signal

import pytest

pytest.importorskip("polign_recall")


@pytest.fixture
def provider(plugin, hermes_home, secrets):
    secrets["RECALL_EXTRACT_MODEL"] = "none"
    p = plugin.RecallMemoryProvider()
    p.initialize("session-live", hermes_home=str(hermes_home), platform="cli", agent_context="primary")
    assert p.idle(240), "the local database did not start"
    yield p
    p.shutdown()
    # The local database is a detached polign-server that outlives the client;
    # stop the one this test started so it does not linger on the machine.
    try:
        with open(hermes_home / "recall" / "runtime.json") as f:
            os.kill(json.load(f)["pid"], signal.SIGTERM)
    except (OSError, ValueError, KeyError):
        pass


def test_turns_are_remembered_and_recalled(plugin, provider, hermes_home):
    assert (hermes_home / "recall").is_dir(), "memory lives under the profile"
    provider.sync_turn("I moved to Portland last week, and I edit in vim.", "Noted.", session_id="session-live")
    assert provider.idle(120)
    out = json.loads(provider.handle_tool_call("polign_recall_search", {"question": "where does the user live"}))
    assert any("Portland" in m["text"] for m in out["memories"]), out
    provider.on_turn_start(2, "which editor do I use?")
    block = provider.prefetch("which editor do I use?")
    assert block.startswith("## Recall Memory") and "vim" in block
    assert provider.recall_status().count >= 1
    out = json.loads(provider.handle_tool_call("polign_recall_remember", {"text": "My desk is by the window."}))
    assert out.get("kept_as_text") is True
    assert "window" in json.dumps(provider.handle_tool_call("polign_recall_profile", {}))
    out = json.loads(provider.handle_tool_call("polign_recall_history", {"question": "desk"}))
    assert "history" in out
