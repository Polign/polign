"""LangChain's standard vector store suite, plus polign-specific behaviour."""

import uuid
from collections.abc import Generator

import pytest
from langchain_core.documents import Document
from langchain_core.vectorstores import VectorStore
from langchain_tests.integration_tests import VectorStoreIntegrationTests

from langchain_polign import PolignVectorStore


def _fresh_store(url: str, embedding) -> PolignVectorStore:
    # Collections cannot be dropped without -byo-store, so each test uses a
    # fresh collection name; the server is per-session and thrown away.
    return PolignVectorStore(embedding=embedding, collection=f"lc-{uuid.uuid4().hex}", url=url)


class TestPolignStandard(VectorStoreIntegrationTests):
    @pytest.fixture()
    def vectorstore(self, polign_url: str) -> Generator[VectorStore, None, None]:
        store = _fresh_store(polign_url, self.get_embeddings())
        try:
            yield store
        finally:
            store.client.close()


@pytest.fixture()
def store(polign_url: str) -> Generator[PolignVectorStore, None, None]:
    s = _fresh_store(polign_url, VectorStoreIntegrationTests.get_embeddings())
    try:
        yield s
    finally:
        s.client.close()


def test_metadata_round_trip(store: PolignVectorStore) -> None:
    metadata = {
        "lang": "en",
        "score": 0.85,
        "published": True,
        "tags": ["a", "b"],
        "nested": {"page": 3, "loc": {"lines": [1, 2]}},
        "missing": None,
    }
    [id] = store.add_texts(["hello world"], metadatas=[metadata], ids=["m1"])
    [doc] = store.get_by_ids([id])
    assert doc == Document(id="m1", page_content="hello world", metadata=metadata)
    # scalars and flat lists stay filterable; the text key is not exposed
    assert [d.id for d in store.similarity_search("hello", k=5, filter={"tags": {"$in": ["b"]}})] == ["m1"]
    assert store.similarity_search("hello", k=5, filter={"lang": "fr"}) == []


def test_reserved_keys_rejected(store: PolignVectorStore) -> None:
    with pytest.raises(ValueError):
        store.add_texts(["x"], metadatas=[{"text": "no"}])
    with pytest.raises(ValueError):
        store.add_texts(["x"], metadatas=[{"_lc_json_keys": ["no"]}])


def test_delete_requires_target(store: PolignVectorStore) -> None:
    with pytest.raises(ValueError):
        store.delete()


def test_delete_by_filter(store: PolignVectorStore) -> None:
    store.add_texts(["a", "b", "c"], metadatas=[{"doc": "1"}, {"doc": "1"}, {"doc": "2"}], ids=["1", "2", "3"])
    store.delete(filter={"doc": "1"})
    assert [d.id for d in store.similarity_search("a", k=10)] == ["3"]


def test_scores_and_relevance(store: PolignVectorStore) -> None:
    store.add_texts(["foo", "bar"], ids=["1", "2"])
    scored = store.similarity_search_with_score("foo", k=2)
    assert [d.id for d, _ in scored] == ["1", "2"]
    assert scored[0][1] <= scored[1][1]  # distance, smaller is closer
    relevance = store.similarity_search_with_relevance_scores("foo", k=2)
    assert relevance[0][1] >= relevance[1][1]
    assert all(0.0 <= r <= 1.0 for _, r in relevance)


def test_mmr(store: PolignVectorStore) -> None:
    store.add_texts(["foo", "foo", "bar", "baz"], ids=["1", "2", "3", "4"])
    docs = store.max_marginal_relevance_search("foo", k=3, fetch_k=4, lambda_mult=0.5)
    assert len(docs) == 3
    assert docs[0].id in ("1", "2")
    assert {d.page_content for d in docs} >= {"foo"}


def test_add_embeddings(store: PolignVectorStore) -> None:
    emb = store.embeddings.embed_documents(["pre-embedded"])
    assert store.add_embeddings(["pre-embedded"], emb, ids=["p"]) == ["p"]
    assert store.get_by_ids(["p"])[0].page_content == "pre-embedded"


def test_lexical_search_needs_segment_store(store: PolignVectorStore) -> None:
    from polign import InvalidArgumentError

    store.add_texts(["quick brown fox"], ids=["1"])
    with pytest.raises(InvalidArgumentError):
        store.lexical_search("fox")


def test_retriever(store: PolignVectorStore) -> None:
    store.add_texts(["foo", "bar"], ids=["1", "2"])
    retriever = store.as_retriever(search_kwargs={"k": 1})
    assert [d.id for d in retriever.invoke("bar")] == ["2"]


def test_from_texts(polign_url: str) -> None:
    s = PolignVectorStore.from_texts(
        ["one", "two"],
        VectorStoreIntegrationTests.get_embeddings(),
        metadatas=[{"n": 1}, {"n": 2}],
        collection=f"lc-{uuid.uuid4().hex}",
        url=polign_url,
    )
    assert s.similarity_search("two", k=1)[0].metadata == {"n": 2}


def test_delete_by_filter_on_unwritten_collection(store: PolignVectorStore) -> None:
    assert store.delete(filter={"doc": "1"}) is True
