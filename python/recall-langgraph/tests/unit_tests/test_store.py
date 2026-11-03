"""RecallStore over the fake client."""

import pytest

from recall_langgraph import RecallStore
from tests.unit_tests.fake_agents import FakeAgentsClient


@pytest.fixture
def store():
    with RecallStore("tests", client=FakeAgentsClient()) as store:
        yield store


def test_put_get_and_delete(store):
    store.put(("users", "u1"), "prefs", {"editor": "zed", "theme": "dark"})
    item = store.get(("users", "u1"), "prefs")
    assert item.value == {"editor": "zed", "theme": "dark"}
    assert item.namespace == ("users", "u1") and item.key == "prefs"
    created = item.created_at
    store.put(("users", "u1"), "prefs", {"editor": "helix"})
    again = store.get(("users", "u1"), "prefs")
    assert again.value == {"editor": "helix"}
    assert again.created_at == created and again.updated_at >= created
    store.delete(("users", "u1"), "prefs")
    assert store.get(("users", "u1"), "prefs") is None
    assert store.get(("users", "nobody"), "prefs") is None


def test_search_by_prefix_filter_and_query(store):
    store.put(("memories", "u1"), "m1", {"text": "likes jazz", "kind": "music", "weight": 3})
    store.put(("memories", "u1"), "m2", {"text": "lives in Lyon", "kind": "place", "weight": 1})
    store.put(("memories", "u2"), "m3", {"text": "likes jazz and blues", "kind": "music", "weight": 2})
    store.put(("notes",), "n1", {"text": "jazz night"})

    assert {i.key for i in store.search(("memories",))} == {"m1", "m2", "m3"}
    assert {i.key for i in store.search(("memories", "u1"))} == {"m1", "m2"}
    assert {i.key for i in store.search(("memories",), filter={"kind": "music"})} == {"m1", "m3"}
    assert {i.key for i in store.search(("memories",), filter={"weight": {"$gte": 2}})} == {"m1", "m3"}
    assert store.search(("memories",), filter={"kind": "sport"}) == []

    ranked = store.search(("memories",), query="jazz blues")
    assert ranked[0].key == "m3" and ranked[0].score == 1.0
    assert [i.score for i in ranked] == sorted((i.score for i in ranked), reverse=True)

    assert len(store.search(("memories",), limit=2)) == 2
    assert len(store.search(("memories",), limit=2, offset=2)) == 1


def test_list_namespaces(store):
    for ns in (("a", "b", "c"), ("a", "b", "d"), ("a", "x"), ("z", "b")):
        store.put(ns, "k", {"v": 1})
    assert store.list_namespaces() == [("a", "b", "c"), ("a", "b", "d"), ("a", "x"), ("z", "b")]
    assert store.list_namespaces(prefix=("a", "b")) == [("a", "b", "c"), ("a", "b", "d")]
    assert store.list_namespaces(suffix=("b",)) == [("z", "b")]
    assert store.list_namespaces(prefix=("*", "b")) == [("a", "b", "c"), ("a", "b", "d"), ("z", "b")]
    assert store.list_namespaces(max_depth=2) == [("a", "b"), ("a", "x"), ("z", "b")]
    assert store.list_namespaces(limit=1, offset=1) == [("a", "b", "d")]


def test_a_batch_puts_then_reads_in_one_lease():
    from langgraph.store.base import GetOp, PutOp
    client = FakeAgentsClient()
    with RecallStore("tests", client=client) as store:
        before = client.resumes
        results = store.batch([PutOp(("n",), "a", {"v": 1}), PutOp(("n",), "b", {"v": 2}), GetOp(("n",), "a")])
        assert results[2].value == {"v": 1}
        # One deferred resume, acquired for the puts; released, so the read resumes again.
        assert client.resumes - before == 2
        assert client.held == {}


async def test_async_methods(store):
    await store.aput(("async",), "k", {"v": 1})
    assert (await store.aget(("async",), "k")).value == {"v": 1}
    assert [i.key for i in await store.asearch(("async",))] == ["k"]
    assert await store.alist_namespaces(prefix=("async",)) == [("async",)]


def test_invalid_namespaces_are_refused(store):
    from langgraph.store.base import InvalidNamespaceError
    with pytest.raises(InvalidNamespaceError):
        store.put((), "k", {"v": 1})
    with pytest.raises(InvalidNamespaceError):
        store.put(("a.b",), "k", {"v": 1})
