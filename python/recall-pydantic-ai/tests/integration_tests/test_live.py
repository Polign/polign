"""End to end against a real Recall: the ``recall`` binary from the
polign-recall wheel and the ``polign-server`` it starts for a local directory.
Skipped when the binary is not installed."""

import json
import os
import signal

import pytest
from polign_recall import Client
from polign_recall.client import recall_bin
from pydantic_ai import Agent

from recall_pydantic_ai import RecallMemory

from ..scripted import Scripted

os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")

pytestmark = pytest.mark.skipif(recall_bin() is None, reason="the recall binary is not installed")


def _open(data, **kwargs):
    try:
        with Client(local_dir=data, **kwargs) as c:
            yield c
    finally:
        # local_dir starts a detached polign-server that outlives the client.
        try:
            with open(data / "runtime.json") as f:
                os.kill(json.load(f)["pid"], signal.SIGTERM)
        except (OSError, ValueError, KeyError):
            pass


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    yield from _open(tmp_path_factory.mktemp("recall-data"), typed=True)


@pytest.fixture(scope="module")
def text_client(tmp_path_factory):
    # No extraction model: text is kept as written and replaces nothing.
    yield from _open(tmp_path_factory.mktemp("recall-text"))


def test_correction_across_runs_keeps_history(client):
    script = Scripted()
    agent = Agent(script.model(), instructions="Be brief.", capabilities=[RecallMemory(client, "live-user")])

    script.calls = [("remember", {"predicate": "name", "value": "Alex"}),
                    ("remember", {"predicate": "uses_technology", "value": "Postgres"})]
    out = agent.run_sync("I'm Alex and we use Postgres").output
    assert "Remembered name: Alex." in out and "Remembered uses_technology: Postgres." in out

    script.calls = [("remember", {"predicate": "name", "value": "Sam"})]
    assert agent.run_sync("actually, call me Sam").output == "Updated name to Sam; it was Alex."

    agent.run_sync("hi")
    block = script.seen[-1].instructions
    assert "- name: Sam" in block and "Alex" not in block
    assert "- uses_technology: Postgres" in block

    values = [e.value for e in client.history("live-user", "name")]
    assert "Alex" in values and "Sam" in values


def test_forget_withdraws(client):
    script = Scripted(calls=[("remember", {"predicate": "timezone", "value": "America/Denver"})])
    memory = RecallMemory(client, "live-forget", forget_tool=True)
    agent = Agent(script.model(), capabilities=[memory])
    agent.run_sync("I'm in Denver")

    script.calls = [("forget", {"predicate": "timezone", "value": "America/Denver"})]
    assert agent.run_sync("forget my timezone").output == "Withdrew 1 remembered value(s) of timezone."
    agent.run_sync("hi")
    assert "Nothing is remembered" in script.seen[-1].instructions


def test_subjects_are_separate(client):
    client.remember("live-a", "name", "Ana")
    script = Scripted()
    Agent(script.model(), capabilities=[RecallMemory(client, "live-b")]).run_sync("hi")
    assert "Ana" not in script.seen[0].instructions


def test_text_remember_is_seen_next_run(text_client):
    memory = RecallMemory(text_client, forget_tool=True)
    assert memory.text
    script = Scripted(calls=[("remember", {"text": "My editor is Helix and I live in Lisbon."})])
    agent = Agent(script.model(), capabilities=[memory])
    assert agent.run_sync("I use Helix, in Lisbon").output == "Kept as written."

    agent.run_sync("hi")
    assert "- My editor is Helix and I live in Lisbon." in script.seen[-1].instructions


def test_text_forget_without_extraction_is_told_to_the_model(text_client):
    script = Scripted(calls=[("forget", {"text": "my editor"})])
    out = Agent(script.model(), capabilities=[RecallMemory(text_client, forget_tool=True)]).run_sync("forget it").output
    assert "Nothing was withdrawn" in out and "extract-model" in out
