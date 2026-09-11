"""Tests against a live polign-server (see conftest.py for how it is found)."""

import uuid

import pytest
from llama_index.core.schema import NodeRelationship, RelatedNodeInfo, TextNode
from llama_index.core.vector_stores.types import (
    BasePydanticVectorStore,
    FilterCondition,
    FilterOperator,
    MetadataFilter,
    MetadataFilters,
    VectorStoreQuery,
    VectorStoreQueryMode,
)

from llama_index.vector_stores.polign import PolignVectorStore


def test_class() -> None:
    names_of_base_classes = [b.__name__ for b in PolignVectorStore.__mro__]
    assert BasePydanticVectorStore.__name__ in names_of_base_classes
    assert PolignVectorStore.class_name() == "PolignVectorStore"


def _nodes() -> list[TextNode]:
    return [
        TextNode(
            text="test1",
            id_="n1",
            embedding=[1.0, 0.0],
            metadata={"some_key": 1, "tags": ["a", "b"], "nested": {"x": 1}},
            relationships={NodeRelationship.SOURCE: RelatedNodeInfo(node_id="doc-0")},
        ),
        TextNode(
            text="test2",
            id_="n2",
            embedding=[0.0, 1.0],
            metadata={"some_key": 2, "tags": ["b"]},
            relationships={NodeRelationship.SOURCE: RelatedNodeInfo(node_id="doc-0")},
        ),
        TextNode(text="test3", id_="n3", embedding=[1.0, 1.0], metadata={"some_key": "3"}),
    ]


@pytest.fixture()
def store(polign_url: str) -> PolignVectorStore:
    # Collections cannot be dropped without -byo-store, so each test gets a
    # fresh collection name on the per-session throwaway server.
    s = PolignVectorStore(collection_name=f"li-{uuid.uuid4().hex}", url=polign_url)
    assert s.add(_nodes()) == ["n1", "n2", "n3"]
    return s


def test_add_requires_embedding(polign_url: str) -> None:
    s = PolignVectorStore(collection_name=f"li-{uuid.uuid4().hex}", url=polign_url)
    with pytest.raises(ValueError):
        s.add([TextNode(text="no embedding", id_="x")])


def test_query_and_round_trip(store: PolignVectorStore) -> None:
    result = store.query(VectorStoreQuery(query_embedding=[1.0, 0.0], similarity_top_k=2))
    assert result.ids == ["n1", "n3"]
    assert result.similarities[0] >= result.similarities[1]
    assert result.similarities[0] == pytest.approx(1.0)  # exact match, 1/(1+0)
    node = result.nodes[0]
    assert node.node_id == "n1"
    assert node.text == "test1"
    assert node.ref_doc_id == "doc-0"
    assert node.metadata == {"some_key": 1, "tags": ["a", "b"], "nested": {"x": 1}}


def test_empty_collection_queries(polign_url: str) -> None:
    s = PolignVectorStore(collection_name=f"li-{uuid.uuid4().hex}", url=polign_url)
    assert s.query(VectorStoreQuery(query_embedding=[1.0, 0.0], similarity_top_k=2)).ids == []
    assert s.get_nodes(node_ids=["n1"]) == []
    s.delete("doc-0")  # no error
    s.clear()


def test_filters(store: PolignVectorStore) -> None:
    def ids(filters: MetadataFilters) -> list[str]:
        r = store.query(VectorStoreQuery(query_embedding=[1.0, 0.0], similarity_top_k=10, filters=filters))
        return sorted(r.ids)

    assert ids(MetadataFilters(filters=[MetadataFilter(key="some_key", value=2)])) == ["n2"]
    assert ids(MetadataFilters(filters=[MetadataFilter(key="some_key", value="3")])) == ["n3"]
    assert ids(MetadataFilters(filters=[MetadataFilter(key="some_key", value=1, operator=FilterOperator.NE)])) == ["n2", "n3"]
    assert ids(MetadataFilters(filters=[MetadataFilter(key="some_key", value=1, operator=FilterOperator.GT)])) == ["n2"]
    assert ids(MetadataFilters(filters=[MetadataFilter(key="some_key", value=[1, 2], operator=FilterOperator.IN)])) == ["n1", "n2"]
    assert ids(MetadataFilters(filters=[MetadataFilter(key="some_key", value=[1, 2], operator=FilterOperator.NIN)])) == ["n3"]
    assert ids(MetadataFilters(filters=[MetadataFilter(key="tags", value="a", operator=FilterOperator.CONTAINS)])) == ["n1"]
    assert ids(MetadataFilters(filters=[MetadataFilter(key="tags", value=["a", "b"], operator=FilterOperator.ANY)])) == ["n1", "n2"]
    assert ids(MetadataFilters(filters=[MetadataFilter(key="tags", value=["a", "b"], operator=FilterOperator.ALL)])) == ["n1"]
    assert ids(MetadataFilters(filters=[MetadataFilter(key="tags", value=None, operator=FilterOperator.IS_EMPTY)])) == ["n3"]
    assert ids(MetadataFilters(
        filters=[MetadataFilter(key="some_key", value=1), MetadataFilter(key="some_key", value=2)],
        condition=FilterCondition.OR,
    )) == ["n1", "n2"]
    assert ids(MetadataFilters(
        filters=[MetadataFilter(key="some_key", value=1)],
        condition=FilterCondition.NOT,
    )) == ["n2", "n3"]
    nested = MetadataFilters(filters=[
        MetadataFilter(key="tags", value="b", operator=FilterOperator.CONTAINS),
        MetadataFilters(filters=[MetadataFilter(key="some_key", value=1), MetadataFilter(key="some_key", value=2)], condition=FilterCondition.OR),
    ])
    assert ids(nested) == ["n1", "n2"]
    with pytest.raises(NotImplementedError):
        ids(MetadataFilters(filters=[MetadataFilter(key="tags", value="a", operator=FilterOperator.TEXT_MATCH)]))


def test_doc_ids_and_node_ids(store: PolignVectorStore) -> None:
    r = store.query(VectorStoreQuery(query_embedding=[1.0, 1.0], similarity_top_k=10, doc_ids=["doc-0"]))
    assert sorted(r.ids) == ["n1", "n2"]
    r = store.query(VectorStoreQuery(query_embedding=[1.0, 1.0], similarity_top_k=10, node_ids=["n3", "n2"]))
    assert sorted(r.ids) == ["n2", "n3"]


def test_get_nodes(store: PolignVectorStore) -> None:
    assert [n.node_id for n in store.get_nodes(node_ids=["n3", "n1", "missing"])] == ["n3", "n1"]
    by_filter = store.get_nodes(filters=MetadataFilters(filters=[MetadataFilter(key="tags", value="b", operator=FilterOperator.CONTAINS)]))
    assert sorted(n.node_id for n in by_filter) == ["n1", "n2"]
    both = store.get_nodes(node_ids=["n1", "n3"], filters=MetadataFilters(filters=[MetadataFilter(key="tags", value="b", operator=FilterOperator.CONTAINS)]))
    assert [n.node_id for n in both] == ["n1"]
    assert len(store.get_nodes()) == 3


def test_delete_by_ref_doc_id(store: PolignVectorStore) -> None:
    store.delete("doc-0")
    assert [n.node_id for n in store.get_nodes()] == ["n3"]


def test_delete_nodes(store: PolignVectorStore) -> None:
    store.delete_nodes(node_ids=["n1"])
    assert sorted(n.node_id for n in store.get_nodes()) == ["n2", "n3"]
    store.delete_nodes(filters=MetadataFilters(filters=[MetadataFilter(key="some_key", value="3")]))
    assert [n.node_id for n in store.get_nodes()] == ["n2"]


def test_clear(store: PolignVectorStore) -> None:
    store.clear()
    assert store.get_nodes() == []
    assert store.query(VectorStoreQuery(query_embedding=[1.0, 0.0], similarity_top_k=2)).ids == []


def test_upsert_replaces(store: PolignVectorStore) -> None:
    store.add([TextNode(text="test1 v2", id_="n1", embedding=[0.0, 1.0], metadata={"some_key": 9})])
    [node] = store.get_nodes(node_ids=["n1"])
    assert node.text == "test1 v2" and node.metadata == {"some_key": 9}
    assert len(store.get_nodes()) == 3


def test_mmr(store: PolignVectorStore) -> None:
    store.add([TextNode(text="dup", id_="n1b", embedding=[1.0, 0.0])])
    r = store.query(VectorStoreQuery(query_embedding=[1.0, 0.0], similarity_top_k=2, mode=VectorStoreQueryMode.MMR, mmr_threshold=0.3))
    assert len(r.ids) == 2 and r.ids[0] in ("n1", "n1b")
    assert r.ids[1] not in ("n1", "n1b")  # MMR prefers a diverse second pick


def test_text_modes_need_segment_store(store: PolignVectorStore) -> None:
    from polign import InvalidArgumentError

    with pytest.raises(InvalidArgumentError):
        store.query(VectorStoreQuery(query_str="test1", similarity_top_k=2, mode=VectorStoreQueryMode.TEXT_SEARCH))
    with pytest.raises(ValueError):
        store.query(VectorStoreQuery(query_embedding=[1.0, 0.0], similarity_top_k=2, mode=VectorStoreQueryMode.HYBRID))


def test_unsupported_mode(store: PolignVectorStore) -> None:
    with pytest.raises(NotImplementedError):
        store.query(VectorStoreQuery(query_embedding=[1.0, 0.0], similarity_top_k=2, mode=VectorStoreQueryMode.SVM))


async def test_async_paths(polign_url: str) -> None:
    s = PolignVectorStore(collection_name=f"li-{uuid.uuid4().hex}", url=polign_url)
    assert await s.async_add(_nodes()) == ["n1", "n2", "n3"]
    r = await s.aquery(VectorStoreQuery(query_embedding=[0.0, 1.0], similarity_top_k=1))
    assert r.ids == ["n2"]
    await s.adelete("doc-0")
    assert [n.node_id for n in await s.aget_nodes()] == ["n3"]
    await s.aclear()
    assert await s.aget_nodes() == []


def test_index_round_trip(polign_url: str) -> None:
    """The store through VectorStoreIndex with a mock embedding model."""
    from llama_index.core import Document, StorageContext, VectorStoreIndex
    from llama_index.core.embeddings import MockEmbedding

    s = PolignVectorStore(collection_name=f"li-{uuid.uuid4().hex}", url=polign_url)
    ctx = StorageContext.from_defaults(vector_store=s)
    docs = [Document(text="cats purr", doc_id="cats"), Document(text="dogs bark", doc_id="dogs")]
    index = VectorStoreIndex.from_documents(docs, storage_context=ctx, embed_model=MockEmbedding(embed_dim=8))
    hits = index.as_retriever(similarity_top_k=2).retrieve("purring")
    assert {h.node.ref_doc_id for h in hits} == {"cats", "dogs"}
    index.delete_ref_doc("cats")
    assert {n.ref_doc_id for n in s.get_nodes()} == {"dogs"}
    again = VectorStoreIndex.from_vector_store(s, embed_model=MockEmbedding(embed_dim=8))
    assert [h.node.ref_doc_id for h in again.as_retriever(similarity_top_k=5).retrieve("x")] == ["dogs"]
