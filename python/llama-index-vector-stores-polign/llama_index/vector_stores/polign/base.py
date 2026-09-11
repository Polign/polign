"""LlamaIndex ``BasePydanticVectorStore`` backed by a polign_db collection.

Layout of one stored record:

- the polign record id is the node id;
- the node text is stored in the metadata key named by ``text_key``
  (default ``"text"``), which is also the field polign's BM25 index reads,
  so ``TEXT_SEARCH`` and ``HYBRID`` queries work without extra setup;
- the rest of the record is what ``node_to_metadata_dict`` produces: the
  node itself as JSON under ``_node_content``, its type under
  ``_node_type``, ``ref_doc_id`` (also as ``doc_id`` and ``document_id``),
  and the node's own metadata at the top level so it can be filtered on.
  ``_node_id`` holds the node id so queries can restrict by ``node_ids``.

Top-level metadata values that polign cannot store (nested objects, ``None``,
lists of objects) are JSON-encoded as strings. The original values are
unaffected, because they also travel inside ``_node_content``.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Callable, Dict, List, Optional, Sequence

from llama_index.core.bridge.pydantic import PrivateAttr
from llama_index.core.indices.query.embedding_utils import get_top_k_mmr_embeddings
from llama_index.core.schema import BaseNode, MetadataMode
from llama_index.core.vector_stores.types import (
    BasePydanticVectorStore,
    FilterCondition,
    FilterOperator,
    MetadataFilter,
    MetadataFilters,
    VectorStoreQuery,
    VectorStoreQueryMode,
    VectorStoreQueryResult,
)
from llama_index.core.vector_stores.utils import (
    DEFAULT_DOC_ID_KEY,
    metadata_dict_to_node,
    node_to_metadata_dict,
)
from polign import Client, Fusion, Vector, errors

logger = logging.getLogger(__name__)

NODE_ID_KEY = "_node_id"
NODE_TYPE_KEY = "_node_type"
_BATCH = 5000  # server-side cap for put_many / delete_many
_SCALARS = (str, bool, int, float)
_DEFAULT_MMR_PREFETCH = 4


def _is_plain(value: Any) -> bool:
    if isinstance(value, _SCALARS):
        return True
    if isinstance(value, (list, tuple)):
        return all(isinstance(item, _SCALARS) for item in value)
    return False


def _chunks(items: Sequence[Any], size: int):
    for start in range(0, len(items), size):
        yield items[start : start + size]


def _l2_similarity(distance: float) -> float:
    return 1.0 / (1.0 + distance)


def _cosine_similarity(distance: float) -> float:
    return 1.0 - distance


class PolignVectorStore(BasePydanticVectorStore):
    """A LlamaIndex vector store over one polign_db collection.

    Example:
        .. code-block:: python

            from llama_index.core import StorageContext, VectorStoreIndex
            from llama_index.vector_stores.polign import PolignVectorStore

            store = PolignVectorStore(collection_name="docs", url="http://localhost:23000")
            index = VectorStoreIndex.from_documents(
                documents, storage_context=StorageContext.from_defaults(vector_store=store)
            )
            index.as_retriever(similarity_top_k=5).retrieve("what purrs?")

    The collection is created on the first write and takes its dimension
    from the first node's embedding.

    Args:
        collection_name: polign collection name.
        url: HTTP listener of the server. Ignored when ``client`` is given.
        api_key: bearer key, when the server requires one.
        timeout: per-request timeout in seconds.
        text_key: metadata key holding the node text. Keep the default
            unless the server's lexical index reads a different field.
        batch_size: records per write request (at most 5000).
        client: an existing ``polign.Client`` or ``polign.GrpcClient``.
        similarity_fn: maps a polign distance to a similarity. The default
            depends on the collection metric: ``1 - d`` for cosine and
            ``1 / (1 + d)`` for L2, which is what the server uses unless
            configured otherwise.
    """

    stores_text: bool = True
    flat_metadata: bool = False

    collection_name: str
    url: str = "http://localhost:23000"
    api_key: Optional[str] = None
    timeout: float = 30.0
    text_key: str = "text"
    batch_size: int = _BATCH
    mmr_prefetch_factor: int = _DEFAULT_MMR_PREFETCH

    _client: Any = PrivateAttr()
    _similarity_fn: Optional[Callable[[float], float]] = PrivateAttr(default=None)
    _metric: Optional[str] = PrivateAttr(default=None)

    def __init__(
        self,
        collection_name: str,
        url: str = "http://localhost:23000",
        api_key: Optional[str] = None,
        timeout: float = 30.0,
        text_key: str = "text",
        batch_size: int = _BATCH,
        client: Optional[Any] = None,
        similarity_fn: Optional[Callable[[float], float]] = None,
        **kwargs: Any,
    ) -> None:
        if not collection_name:
            raise ValueError("collection_name must be a non-empty name")
        if not text_key:
            raise ValueError("text_key must be a non-empty key")
        if not 0 < batch_size <= _BATCH:
            raise ValueError(f"batch_size must be between 1 and {_BATCH}")
        super().__init__(
            collection_name=collection_name,
            url=url,
            api_key=api_key,
            timeout=timeout,
            text_key=text_key,
            batch_size=batch_size,
            **kwargs,
        )
        self._client = client or Client(url, api_key=api_key, timeout=timeout)
        self._similarity_fn = similarity_fn

    @classmethod
    def class_name(cls) -> str:
        return "PolignVectorStore"

    @property
    def client(self) -> Any:
        """The underlying polign client."""
        return self._client

    # -- record mapping -----------------------------------------------------

    def _encode(self, node: BaseNode) -> Dict[str, Any]:
        stored = node_to_metadata_dict(node, remove_text=True, text_field=self.text_key)
        out: Dict[str, Any] = {}
        for key, value in stored.items():
            if key == self.text_key:
                continue
            out[key] = value if _is_plain(value) else json.dumps(value, ensure_ascii=False)
        out[NODE_ID_KEY] = node.node_id
        out[self.text_key] = node.get_content(metadata_mode=MetadataMode.NONE)
        return out

    def _decode(self, stored: Dict[str, Any]) -> BaseNode:
        text = stored.get(self.text_key)
        node = metadata_dict_to_node(stored, text=text if isinstance(text, str) else None)
        return node

    def _similarity(self, distance: float) -> float:
        if self._similarity_fn is not None:
            return self._similarity_fn(distance)
        if self._metric is None:
            try:
                self._metric = self._client.describe_collection(self.collection_name).metric or "l2"
            except errors.PolignError:
                self._metric = "l2"
        if self._metric == "cosine":
            return _cosine_similarity(distance)
        return _l2_similarity(distance)

    # -- filters ------------------------------------------------------------

    def _translate_filter(self, f: MetadataFilter) -> Dict[str, Any]:
        key, value, op = f.key, f.value, f.operator
        if op in (None, FilterOperator.EQ, FilterOperator.CONTAINS):
            # Equality on a list-valued key means containment in polign.
            return {key: {"$eq": value}}
        if op == FilterOperator.NE:
            return {key: {"$ne": value}}
        if op == FilterOperator.GT:
            return {key: {"$gt": value}}
        if op == FilterOperator.GTE:
            return {key: {"$gte": value}}
        if op == FilterOperator.LT:
            return {key: {"$lt": value}}
        if op == FilterOperator.LTE:
            return {key: {"$lte": value}}
        if op in (FilterOperator.IN, FilterOperator.ANY):
            return {key: {"$in": list(value) if isinstance(value, (list, tuple)) else [value]}}
        if op == FilterOperator.NIN:
            values = list(value) if isinstance(value, (list, tuple)) else [value]
            return {"$not": {key: {"$in": values}}}
        if op == FilterOperator.ALL:
            values = list(value) if isinstance(value, (list, tuple)) else [value]
            return {"$and": [{key: {"$eq": v}} for v in values]}
        if op == FilterOperator.IS_EMPTY:
            return {key: {"$exists": False}}
        raise NotImplementedError(
            f"filter operator {op} is not supported by polign; supported: "
            "EQ, NE, GT, GTE, LT, LTE, IN, NIN, ANY, ALL, CONTAINS, IS_EMPTY"
        )

    def _translate_filters(self, filters: MetadataFilters) -> Optional[Dict[str, Any]]:
        parts: List[Dict[str, Any]] = []
        for sub in filters.filters:
            if isinstance(sub, MetadataFilters):
                translated = self._translate_filters(sub)
                if translated:
                    parts.append(translated)
            else:
                parts.append(self._translate_filter(sub))
        if not parts:
            return None
        condition = filters.condition or FilterCondition.AND
        if condition == FilterCondition.AND:
            return parts[0] if len(parts) == 1 else {"$and": parts}
        if condition == FilterCondition.OR:
            return parts[0] if len(parts) == 1 else {"$or": parts}
        if condition == FilterCondition.NOT:
            return {"$not": parts[0] if len(parts) == 1 else {"$and": parts}}
        raise NotImplementedError(f"filter condition {condition} is not supported")

    def _query_filter(self, query: VectorStoreQuery) -> Optional[Dict[str, Any]]:
        parts: List[Dict[str, Any]] = []
        if query.doc_ids:
            parts.append({DEFAULT_DOC_ID_KEY: {"$in": list(query.doc_ids)}})
        if query.node_ids:
            parts.append({NODE_ID_KEY: {"$in": list(query.node_ids)}})
        if query.filters:
            translated = self._translate_filters(query.filters)
            if translated:
                parts.append(translated)
        if not parts:
            return None
        return parts[0] if len(parts) == 1 else {"$and": parts}

    # -- writes -------------------------------------------------------------

    def add(self, nodes: Sequence[BaseNode], **add_kwargs: Any) -> List[str]:
        """Upsert nodes; every node needs an embedding. Returns their ids."""
        vectors = []
        for node in nodes:
            if node.embedding is None:
                raise ValueError(f"node {node.node_id} has no embedding")
            vectors.append(
                Vector(id=node.node_id, values=list(node.embedding), metadata=self._encode(node))
            )
        for chunk in _chunks(vectors, self.batch_size):
            self._client.put_many(self.collection_name, chunk)
        return [v.id for v in vectors]

    async def async_add(self, nodes: Sequence[BaseNode], **kwargs: Any) -> List[str]:
        return await asyncio.to_thread(self.add, nodes, **kwargs)

    def _collection_exists(self) -> bool:
        try:
            self._client.describe_collection(self.collection_name)
        except errors.NotFoundError:
            return False
        return True

    def _delete_by_filter(self, filter: Dict[str, Any]) -> None:
        # A filter delete on a collection that was never written is an
        # invalid-argument error server-side, not a not-found; check first.
        if not self._collection_exists():
            return
        try:
            while True:
                result = self._client.delete_many(self.collection_name, filter=filter)
                if not getattr(result, "truncated", False):
                    return
        except errors.NotFoundError:
            return

    def delete(self, ref_doc_id: str, **delete_kwargs: Any) -> None:
        """Delete every node that came from ``ref_doc_id``, hot or cold."""
        self._delete_by_filter({DEFAULT_DOC_ID_KEY: {"$eq": ref_doc_id}})

    async def adelete(self, ref_doc_id: str, **delete_kwargs: Any) -> None:
        await asyncio.to_thread(self.delete, ref_doc_id, **delete_kwargs)

    def delete_nodes(
        self,
        node_ids: Optional[List[str]] = None,
        filters: Optional[MetadataFilters] = None,
        **delete_kwargs: Any,
    ) -> None:
        """Delete by node ids, by metadata filters, or both (union)."""
        if node_ids:
            try:
                for chunk in _chunks(list(node_ids), _BATCH):
                    self._client.delete_many(self.collection_name, ids=chunk)
            except errors.NotFoundError:
                pass
        if filters:
            translated = self._translate_filters(filters)
            if translated:
                self._delete_by_filter(translated)

    async def adelete_nodes(
        self,
        node_ids: Optional[List[str]] = None,
        filters: Optional[MetadataFilters] = None,
        **delete_kwargs: Any,
    ) -> None:
        await asyncio.to_thread(self.delete_nodes, node_ids, filters, **delete_kwargs)

    def clear(self) -> None:
        """Delete every node in the collection.

        Dropping the collection itself needs the server's ``-byo-store``
        collection API, so this removes the records instead.
        """
        self._delete_by_filter({NODE_TYPE_KEY: {"$exists": True}})

    async def aclear(self) -> None:
        await asyncio.to_thread(self.clear)

    # -- reads --------------------------------------------------------------

    def get_nodes(
        self,
        node_ids: Optional[List[str]] = None,
        filters: Optional[MetadataFilters] = None,
        limit: Optional[int] = None,
    ) -> List[BaseNode]:
        """Fetch nodes by id, by filters, or both (intersection)."""
        translated = self._translate_filters(filters) if filters else None
        try:
            if node_ids is not None:
                nodes: List[BaseNode] = []
                for chunk in _chunks(list(node_ids), _BATCH):
                    for v in self._client.get_many(self.collection_name, chunk, typed_metadata=True):
                        nodes.append(self._decode(v.metadata))
                if translated:
                    keep = {v.id for v in self._list_all(translated)}
                    nodes = [n for n in nodes if n.node_id in keep]
                return nodes[:limit] if limit else nodes
            return [self._decode(v.metadata) for v in self._list_all(translated, limit)]
        except errors.NotFoundError:
            return []

    def _list_all(self, filter: Optional[Dict[str, Any]], limit: Optional[int] = None) -> List[Vector]:
        out: List[Vector] = []
        offset = 0
        page_size = 1000
        while True:
            page = self._client.list(
                self.collection_name, limit=page_size, offset=offset, filter=filter, typed_metadata=True
            )
            out.extend(page.vectors)
            offset += len(page.vectors)
            if limit and len(out) >= limit:
                return out[:limit]
            if not page.vectors or offset >= page.total:
                return out

    async def aget_nodes(
        self,
        node_ids: Optional[List[str]] = None,
        filters: Optional[MetadataFilters] = None,
    ) -> List[BaseNode]:
        return await asyncio.to_thread(self.get_nodes, node_ids, filters)

    def query(self, query: VectorStoreQuery, **kwargs: Any) -> VectorStoreQueryResult:
        """Run a vector, lexical, hybrid, or MMR query.

        Modes: ``DEFAULT`` is vector search. ``TEXT_SEARCH`` and ``SPARSE``
        run BM25 over ``text_key``. ``HYBRID`` fuses both legs server-side,
        linearly when ``query.alpha`` is set (``alpha`` weights the vector
        leg) and by reciprocal rank otherwise. ``MMR`` re-ranks
        ``similarity_top_k * mmr_prefetch_factor`` nearest nodes using
        ``query.mmr_threshold``. Lexical and hybrid modes need a server with
        a segment store and only see records already written to a segment.

        Extra keyword arguments ``ef``, ``cold``, ``nprobe`` and ``rescore``
        are passed to ``polign.Client.search`` unchanged.
        """
        filter = self._query_filter(query)
        k = query.similarity_top_k
        search_kwargs = {key: kwargs[key] for key in ("ef", "cold", "nprobe", "rescore") if key in kwargs}
        mode = query.mode

        if mode in (VectorStoreQueryMode.TEXT_SEARCH, VectorStoreQueryMode.SPARSE):
            if not query.query_str:
                raise ValueError(f"{mode} needs query_str")
            top_k = query.sparse_top_k or k
            hits = self._search(text=query.query_str, k=top_k, filter=filter)
            return self._result(hits, [float(h.score) for h in hits])

        if mode == VectorStoreQueryMode.HYBRID:
            if not query.query_str or query.query_embedding is None:
                raise ValueError("HYBRID needs both query_str and query_embedding")
            fusion = Fusion(method="linear", alpha=query.alpha) if query.alpha is not None else None
            top_k = query.hybrid_top_k or k
            hits = self._search(
                values=query.query_embedding, text=query.query_str, k=top_k, filter=filter, fusion=fusion
            )
            return self._result(hits, [float(h.score) for h in hits])

        if mode not in (VectorStoreQueryMode.DEFAULT, VectorStoreQueryMode.MMR):
            raise NotImplementedError(f"query mode {mode} is not supported by polign")
        if query.query_embedding is None:
            raise ValueError("vector search needs query_embedding")

        if mode == VectorStoreQueryMode.MMR:
            fetch_k = max(k * max(self.mmr_prefetch_factor, 1), k)
            hits = self._search(values=query.query_embedding, k=fetch_k, filter=filter, **search_kwargs)
            if not hits:
                return VectorStoreQueryResult(nodes=[], similarities=[], ids=[])
            stored = self._client.get_many(
                self.collection_name, [h.id for h in hits], typed_metadata=True
            )
            by_id = {v.id: v for v in stored}
            candidates = [by_id[h.id] for h in hits if h.id in by_id]
            similarities, ids = get_top_k_mmr_embeddings(
                list(query.query_embedding),
                [list(v.values) for v in candidates],
                similarity_top_k=k,
                embedding_ids=[v.id for v in candidates],
                mmr_threshold=query.mmr_threshold,
            )
            nodes = [self._decode(by_id[i].metadata) for i in ids]
            return VectorStoreQueryResult(nodes=nodes, similarities=list(similarities), ids=list(ids))

        hits = self._search(values=query.query_embedding, k=k, filter=filter, **search_kwargs)
        return self._result(hits, [self._similarity(float(h.distance)) for h in hits])

    def _search(self, **kwargs: Any) -> List[Any]:
        try:
            return self._client.search(self.collection_name, typed_metadata=True, **kwargs)
        except errors.NotFoundError:
            return []  # nothing was ever written to this collection

    def _result(self, hits: List[Any], similarities: List[float]) -> VectorStoreQueryResult:
        nodes = [self._decode(h.metadata) for h in hits]
        return VectorStoreQueryResult(nodes=nodes, similarities=similarities, ids=[h.id for h in hits])

    async def aquery(self, query: VectorStoreQuery, **kwargs: Any) -> VectorStoreQueryResult:
        return await asyncio.to_thread(self.query, query, **kwargs)
