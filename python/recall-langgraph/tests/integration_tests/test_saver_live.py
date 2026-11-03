"""RecallSaver and RecallStore end to end: the ``recall`` binary from the
polign-recall wheel and the ``polign-server`` it starts for a local
directory. LangGraph's conformance suite first, then two clients sharing one
thread. Skipped when the binary is not installed."""

import asyncio
import json
import os
import signal
import uuid

import pytest
from langgraph.checkpoint.conformance import checkpointer_test, validate
from langgraph.checkpoint.conformance.report import ProgressCallbacks
from polign_recall import Client
from polign_recall.client import recall_bin

from recall_langgraph import RecallSaver, RecallStore

pytestmark = pytest.mark.skipif(recall_bin() is None, reason="the recall binary is not installed")


@pytest.fixture(scope="module")
def data(tmp_path_factory):
    directory = tmp_path_factory.mktemp("recall-data")
    try:
        yield directory
    finally:
        # local_dir starts a detached polign-server that outlives the clients.
        try:
            with open(directory / "runtime.json") as f:
                os.kill(json.load(f)["pid"], signal.SIGTERM)
        except (OSError, ValueError, KeyError):
            pass


def connect(data):
    return Client(local_dir=data, agent=True, env={"RECALL_COLLECTION": "recall_lgs_" + uuid.uuid4().hex[:8]},
                  timeout=60)


def test_conformance(data):
    @checkpointer_test(name="RecallSaver (polign_db)")
    async def live_saver():
        with connect(data) as client, RecallSaver(client=client, lease_ttl=5) as saver:
            yield saver

    report = asyncio.run(validate(live_saver, progress=ProgressCallbacks.default()))
    report.print_report()
    assert report.passed_all_base()


def test_two_clients_share_a_thread(data):
    env = {"RECALL_COLLECTION": "recall_lgs_" + uuid.uuid4().hex[:8]}
    config = {"configurable": {"thread_id": "shared", "checkpoint_ns": ""}}
    checkpoint = {"v": 1, "id": "1ef00000-0000-6000-8000-000000000001", "ts": "2026-11-03T00:00:00+00:00",
                  "channel_values": {"messages": ["hi"]}, "channel_versions": {"messages": "1"},
                  "versions_seen": {}}
    with Client(local_dir=data, agent=True, env=env) as first, RecallSaver(client=first, lease_ttl=5) as saver:
        stored = saver.put(config, checkpoint, {"source": "input", "step": -1}, {"messages": "1"})
        saver.put_writes(stored, [("messages", "there")], "task-1")
    with Client(local_dir=data, agent=True, env=env) as second, RecallSaver(client=second, lease_ttl=5) as saver:
        found = saver.get_tuple(config)
        assert found.checkpoint["channel_values"] == {"messages": ["hi"]}
        assert found.pending_writes == [("task-1", "messages", "there")]
        assert [t.checkpoint["id"] for t in saver.list(config)] == [checkpoint["id"]]
        saver.delete_thread("shared")
        assert saver.get_tuple(config) is None


def test_store_round_trip(data):
    env = {"RECALL_COLLECTION": "recall_lgs_" + uuid.uuid4().hex[:8]}
    with Client(local_dir=data, agent=True, env=env) as client, RecallStore("live", client=client, lease_ttl=5) as store:
        store.put(("users", "u1"), "prefs", {"editor": "zed"})
        store.put(("users", "u2"), "prefs", {"editor": "vim"})
        assert store.get(("users", "u1"), "prefs").value == {"editor": "zed"}
        assert {i.key for i in store.search(("users",), filter={"editor": "vim"})} == {"prefs"}
        assert store.list_namespaces(prefix=("users",)) == [("users", "u1"), ("users", "u2")]
    with Client(local_dir=data, agent=True, env=env) as client, RecallStore("live", client=client, lease_ttl=5) as store:
        assert store.get(("users", "u2"), "prefs").value == {"editor": "vim"}
