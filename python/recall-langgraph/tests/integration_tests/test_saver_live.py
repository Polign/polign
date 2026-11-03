"""RecallSaver and RecallStore end to end: the ``recall`` binary from the
polign-recall wheel and the ``polign-server`` it starts for a local
directory. LangGraph's conformance suite first, then two clients sharing one
thread. Skipped when the binary is not installed."""

import asyncio

from langgraph.checkpoint.conformance import checkpointer_test, validate
from langgraph.checkpoint.conformance.report import ProgressCallbacks

from recall_langgraph import RecallSaver, RecallStore
from tests.integration_tests.conftest import collection, connect


def test_conformance(data):
    @checkpointer_test(name="RecallSaver (polign_db)")
    async def live_saver():
        with connect(data, collection("lgs")) as client, RecallSaver(client=client, lease_ttl=5) as saver:
            yield saver

    report = asyncio.run(validate(live_saver, progress=ProgressCallbacks.default()))
    report.print_report()
    assert report.passed_all_base()


def test_two_clients_share_a_thread(data):
    shared = collection("lgs")
    config = {"configurable": {"thread_id": "shared", "checkpoint_ns": ""}}
    checkpoint = {"v": 1, "id": "1ef00000-0000-6000-8000-000000000001", "ts": "2026-11-03T00:00:00+00:00",
                  "channel_values": {"messages": ["hi"]}, "channel_versions": {"messages": "1"},
                  "versions_seen": {}}
    with connect(data, shared) as first, RecallSaver(client=first, lease_ttl=5) as saver:
        stored = saver.put(config, checkpoint, {"source": "input", "step": -1}, {"messages": "1"})
        saver.put_writes(stored, [("messages", "there")], "task-1")
    with connect(data, shared) as second, RecallSaver(client=second, lease_ttl=5) as saver:
        found = saver.get_tuple(config)
        assert found.checkpoint["channel_values"] == {"messages": ["hi"]}
        assert found.pending_writes == [("task-1", "messages", "there")]
        assert [t.checkpoint["id"] for t in saver.list(config)] == [checkpoint["id"]]
        saver.delete_thread("shared")
        assert saver.get_tuple(config) is None


def test_store_round_trip(data):
    shared = collection("lgs")
    with connect(data, shared) as client, RecallStore("live", client=client, lease_ttl=5) as store:
        store.put(("users", "u1"), "prefs", {"editor": "zed"})
        store.put(("users", "u2"), "prefs", {"editor": "vim"})
        assert store.get(("users", "u1"), "prefs").value == {"editor": "zed"}
        assert {i.key for i in store.search(("users",), filter={"editor": "vim"})} == {"prefs"}
        assert store.list_namespaces(prefix=("users",)) == [("users", "u1"), ("users", "u2")]
    with connect(data, shared) as client, RecallStore("live", client=client, lease_ttl=5) as store:
        assert store.get(("users", "u2"), "prefs").value == {"editor": "vim"}
