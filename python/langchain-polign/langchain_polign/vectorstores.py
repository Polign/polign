"""LangChain ``VectorStore`` backed by a polign_db collection.

Layout of one stored record:

- the polign record id is the LangChain document id;
- ``page_content`` is stored in the metadata key named by ``text_key``
  (default ``"text"``), which is also the field polign's BM25 index reads,
  so hybrid search works without any extra configuration;
- every other ``Document.metadata`` entry is stored as polign metadata.
  Strings, numbers, booleans, and flat lists of those pass through unchanged
  and stay filterable. Anything else (nested objects, ``None``, lists of
  objects) is JSON-encoded as a string and the key is recorded in the
  reserved ``_lc_json_keys`` list, so reads restore the original value.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterable, Sequence
from typing import Any, Callable

import numpy as np
from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_core.vectorstores import VectorStore
from langchain_core.vectorstores.utils import maximal_marginal_relevance
from polign import Client, Fusion, Vector, errors

JSON_KEYS = "_lc_json_keys"
"""Reserved metadata key listing the keys whose values were JSON-encoded."""

_BATCH = 5000  # server-side cap for put_many / delete_many
_SCALARS = (str, bool, int, float)


def _is_plain(value: Any) -> bool:
    if isinstance(value, _SCALARS):
        return True
    if isinstance(value, (list, tuple)):
        return all(isinstance(item, _SCALARS) for item in value)
    return False


def _chunks(items: Sequence[Any], size: int) -> Iterable[Sequence[Any]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


class PolignVectorStore(VectorStore):
    """A LangChain vector store over one polign_db collection.

    Example:
        .. code-block:: python

            from langchain_openai import OpenAIEmbeddings
            from langchain_polign import PolignVectorStore

            store = PolignVectorStore(
                embedding=OpenAIEmbeddings(),
                collection="docs",
                url="http://localhost:23000",
            )
            store.add_texts(["cats purr", "dogs bark"], metadatas=[{"lang": "en"}] * 2)
            store.similarity_search("purring", k=1, filter={"lang": "en"})

    The collection is created on the first write; its dimension is taken
    from the first vector. ``filter`` on every search method is polign's
    metadata predicate language (``{"lang": "en"}``, ``{"score": {"$gte":
    0.5}}``, ``$and`` / ``$or`` / ``$not``).
    """

    def __init__(
        self,
        embedding: Embeddings,
        collection: str,
        *,
        client: Client | None = None,
        url: str = "http://localhost:23000",
        api_key: str | None = None,
        timeout: float = 30.0,
        text_key: str = "text",
        relevance_score_fn: Callable[[float], float] | None = None,
    ) -> None:
        """Create a store over ``collection``.

        Args:
            embedding: the embedding model used for documents and queries.
            collection: polign collection name.
            client: an existing ``polign.Client`` or ``polign.GrpcClient``.
                When given, ``url``, ``api_key`` and ``timeout`` are ignored.
            url: HTTP listener of the server, used when ``client`` is None.
            api_key: bearer key, when the server requires one.
            timeout: per-request timeout in seconds.
            text_key: metadata key that stores ``page_content``. Keep the
                default unless the server's lexical index reads a different
                field.
            relevance_score_fn: override for the distance-to-relevance
                mapping used by ``similarity_search_with_relevance_scores``.
                The default depends on the collection metric (see
                ``_select_relevance_score_fn``).
        """
        if not collection:
            raise ValueError("collection must be a non-empty name")
        if not text_key or text_key == JSON_KEYS:
            raise ValueError(f"text_key must be a non-empty key other than {JSON_KEYS!r}")
        self._embedding = embedding
        self._collection = collection
        self._client = client or Client(url, api_key=api_key, timeout=timeout)
        self._text_key = text_key
        self._relevance_score_fn = relevance_score_fn
        self._metric: str | None = None

    # -- properties ---------------------------------------------------------

    @property
    def embeddings(self) -> Embeddings:
        return self._embedding

    @property
    def client(self) -> Client:
        """The underlying polign client."""
        return self._client

    @property
    def collection(self) -> str:
        return self._collection

    # -- record mapping -----------------------------------------------------

    def _encode(self, text: str, metadata: dict[str, Any] | None) -> dict[str, Any]:
        out: dict[str, Any] = {}
        json_keys: list[str] = []
        for key, value in (metadata or {}).items():
            if key == self._text_key or key == JSON_KEYS:
                raise ValueError(f"metadata key {key!r} is reserved by PolignVectorStore")
            if _is_plain(value):
                out[key] = list(value) if isinstance(value, tuple) else value
            else:
                out[key] = json.dumps(value)
                json_keys.append(key)
        if json_keys:
            out[JSON_KEYS] = json_keys
        out[self._text_key] = text
        return out

    def _decode(self, id: str, stored: dict[str, Any]) -> Document:
        metadata = dict(stored)
        text = metadata.pop(self._text_key, "")
        json_keys = metadata.pop(JSON_KEYS, None)
        if isinstance(json_keys, list):
            for key in json_keys:
                if isinstance(metadata.get(key), str):
                    metadata[key] = json.loads(metadata[key])
        return Document(id=id, page_content=str(text), metadata=metadata)

    # -- writes -------------------------------------------------------------

    def add_texts(
        self,
        texts: Iterable[str],
        metadatas: list[dict[str, Any]] | None = None,
        *,
        ids: list[str] | None = None,
        **kwargs: Any,
    ) -> list[str]:
        texts = list(texts)
        if not texts:
            return []
        if metadatas is not None and len(metadatas) != len(texts):
            raise ValueError("metadatas must have one entry per text")
        if ids is None:
            ids = [str(uuid.uuid4()) for _ in texts]
        else:
            if len(ids) != len(texts):
                raise ValueError("ids must have one entry per text")
            ids = [i if i else str(uuid.uuid4()) for i in ids]
        embeddings = self._embedding.embed_documents(texts)
        return self._put(ids, texts, embeddings, metadatas)

    def add_embeddings(
        self,
        texts: Sequence[str],
        embeddings: Sequence[Sequence[float]],
        metadatas: list[dict[str, Any]] | None = None,
        *,
        ids: list[str] | None = None,
    ) -> list[str]:
        """Store texts with vectors that were embedded elsewhere."""
        if len(embeddings) != len(texts):
            raise ValueError("embeddings must have one entry per text")
        if metadatas is not None and len(metadatas) != len(texts):
            raise ValueError("metadatas must have one entry per text")
        if ids is None:
            ids = [str(uuid.uuid4()) for _ in texts]
        elif len(ids) != len(texts):
            raise ValueError("ids must have one entry per text")
        return self._put(list(ids), list(texts), embeddings, metadatas)

    def _put(
        self,
        ids: list[str],
        texts: list[str],
        embeddings: Sequence[Sequence[float]],
        metadatas: list[dict[str, Any]] | None,
    ) -> list[str]:
        vectors = [
            Vector(
                id=id,
                values=list(values),
                metadata=self._encode(text, metadatas[i] if metadatas else None),
            )
            for i, (id, text, values) in enumerate(zip(ids, texts, embeddings))
        ]
        for chunk in _chunks(vectors, _BATCH):
            self._client.put_many(self._collection, chunk)
        return ids

    def delete(
        self,
        ids: list[str] | None = None,
        *,
        filter: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> bool | None:
        """Delete by ids, by a metadata filter, or both.

        Deleting ids that do not exist is not an error. A filter delete
        removes every matching record, including ones only held in cold
        segments, repeating the server call while it reports truncation.
        """
        if ids is None and filter is None:
            raise ValueError("delete needs ids or a filter; refusing to delete everything")
        try:
            if ids:
                for chunk in _chunks(list(ids), _BATCH):
                    self._client.delete_many(self._collection, ids=chunk)
            if filter:
                while True:
                    result = self._client.delete_many(self._collection, filter=filter)
                    if not getattr(result, "truncated", False):
                        break
        except errors.NotFoundError:
            return True  # the collection was never written
        return True

    # -- reads --------------------------------------------------------------

    def get_by_ids(self, ids: Sequence[str], /) -> list[Document]:
        ids = list(ids)
        if not ids:
            return []
        docs: list[Document] = []
        try:
            for chunk in _chunks(ids, _BATCH):
                for v in self._client.get_many(self._collection, chunk, typed_metadata=True):
                    docs.append(self._decode(v.id, v.metadata))
        except errors.NotFoundError:
            return []
        return docs

    def similarity_search(
        self, query: str, k: int = 4, *, filter: dict[str, Any] | None = None, **kwargs: Any
    ) -> list[Document]:
        embedding = self._embedding.embed_query(query)
        return self.similarity_search_by_vector(embedding, k, filter=filter, **kwargs)

    def similarity_search_with_score(
        self, query: str, k: int = 4, *, filter: dict[str, Any] | None = None, **kwargs: Any
    ) -> list[tuple[Document, float]]:
        """Return documents with their distance (smaller is closer)."""
        embedding = self._embedding.embed_query(query)
        return self.similarity_search_with_score_by_vector(embedding, k, filter=filter, **kwargs)

    def similarity_search_by_vector(
        self,
        embedding: list[float],
        k: int = 4,
        *,
        filter: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> list[Document]:
        return [
            doc
            for doc, _ in self.similarity_search_with_score_by_vector(
                embedding, k, filter=filter, **kwargs
            )
        ]

    def similarity_search_with_score_by_vector(
        self,
        embedding: list[float],
        k: int = 4,
        *,
        filter: dict[str, Any] | None = None,
        ef: int = 0,
        cold: bool = False,
        nprobe: int = 0,
        rescore: int = 0,
        **kwargs: Any,
    ) -> list[tuple[Document, float]]:
        """Vector search returning ``(document, distance)`` pairs.

        ``ef``, ``cold``, ``nprobe`` and ``rescore`` are passed to
        ``polign.Client.search`` unchanged; see its documentation.
        """
        try:
            hits = self._client.search(
                self._collection,
                values=list(embedding),
                k=k,
                ef=ef,
                cold=cold,
                nprobe=nprobe,
                rescore=rescore,
                filter=filter,
                typed_metadata=True,
            )
        except errors.NotFoundError:
            return []  # nothing was ever written to this collection
        return [(self._decode(h.id, h.metadata), float(h.distance)) for h in hits]

    def _select_relevance_score_fn(self) -> Callable[[float], float]:
        """Map polign distances to a relevance score in ``[0, 1]``.

        The mapping follows the collection metric reported by the server:
        ``cosine`` distance becomes ``1 - d``; ``l2`` becomes ``1 / (1 + d)``,
        which is monotonic and needs no assumption about vector norms. Pass
        ``relevance_score_fn`` to the constructor to use your own.
        """
        if self._relevance_score_fn is not None:
            return self._relevance_score_fn
        metric = self._collection_metric()
        if metric == "cosine":
            return lambda distance: 1.0 - distance
        if metric in ("ip", "dot", "inner_product"):
            return lambda distance: -distance
        return lambda distance: 1.0 / (1.0 + distance)

    def _collection_metric(self) -> str:
        if self._metric is None:
            try:
                self._metric = self._client.describe_collection(self._collection).metric or "l2"
            except errors.PolignError:
                return "l2"
        return self._metric

    # -- lexical and hybrid -------------------------------------------------

    def lexical_search(
        self, query: str, k: int = 4, *, filter: dict[str, Any] | None = None
    ) -> list[tuple[Document, float]]:
        """BM25 search over ``text_key``; returns ``(document, score)``, larger is better.

        Needs a server with a segment store (``polign-server -store ...``);
        a plain in-memory server raises ``polign.InvalidArgumentError``.
        Records become lexically searchable once the server has written
        them to a segment and refreshed its searchers, about half a minute
        with default settings; vector search sees them immediately.
        """
        try:
            hits = self._client.search(
                self._collection, text=query, k=k, filter=filter, typed_metadata=True
            )
        except errors.NotFoundError:
            return []
        return [(self._decode(h.id, h.metadata), float(h.score)) for h in hits]

    def hybrid_search(
        self,
        query: str,
        k: int = 4,
        *,
        filter: dict[str, Any] | None = None,
        alpha: float | None = None,
        rrf_k: int | None = None,
    ) -> list[tuple[Document, float]]:
        """Vector plus BM25 search fused server-side; returns ``(document, score)``.

        With ``alpha`` set, fusion is a linear blend where ``alpha`` weights
        the vector leg. Otherwise reciprocal rank fusion is used, with
        ``rrf_k`` as its constant when given. Needs a segment store like
        ``lexical_search``, and shares its segment latency: only persisted
        records take part in the BM25 leg.
        """
        fusion = None
        if alpha is not None:
            fusion = Fusion(method="linear", alpha=alpha)
        elif rrf_k is not None:
            fusion = Fusion(method="rrf", rrf_k=rrf_k)
        embedding = self._embedding.embed_query(query)
        try:
            hits = self._client.search(
                self._collection,
                values=list(embedding),
                text=query,
                k=k,
                filter=filter,
                fusion=fusion,
                typed_metadata=True,
            )
        except errors.NotFoundError:
            return []
        return [(self._decode(h.id, h.metadata), float(h.score)) for h in hits]

    # -- maximal marginal relevance -----------------------------------------

    def max_marginal_relevance_search(
        self,
        query: str,
        k: int = 4,
        fetch_k: int = 20,
        lambda_mult: float = 0.5,
        *,
        filter: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> list[Document]:
        embedding = self._embedding.embed_query(query)
        return self.max_marginal_relevance_search_by_vector(
            embedding, k, fetch_k, lambda_mult, filter=filter, **kwargs
        )

    def max_marginal_relevance_search_by_vector(
        self,
        embedding: list[float],
        k: int = 4,
        fetch_k: int = 20,
        lambda_mult: float = 0.5,
        *,
        filter: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> list[Document]:
        """MMR over the ``fetch_k`` nearest records.

        Search hits do not carry vectors, so the candidates' vectors are read
        back with one batch ``get_many`` call before re-ranking.
        """
        candidates = self.similarity_search_with_score_by_vector(
            embedding, fetch_k, filter=filter, **kwargs
        )
        if not candidates:
            return []
        stored = self._client.get_many(self._collection, [doc.id for doc, _ in candidates])
        by_id = {v.id: v.values for v in stored}
        docs = [doc for doc, _ in candidates if doc.id in by_id]
        picked = maximal_marginal_relevance(
            np.asarray(embedding, dtype=np.float32),
            [by_id[doc.id] for doc in docs],
            lambda_mult=lambda_mult,
            k=k,
        )
        return [docs[i] for i in picked]

    # -- constructors -------------------------------------------------------

    @classmethod
    def from_texts(
        cls,
        texts: list[str],
        embedding: Embeddings,
        metadatas: list[dict[str, Any]] | None = None,
        *,
        ids: list[str] | None = None,
        **kwargs: Any,
    ) -> PolignVectorStore:
        """Create a store and add ``texts`` to it. ``collection`` is required in kwargs."""
        store = cls(embedding=embedding, **kwargs)
        store.add_texts(texts, metadatas=metadatas, ids=ids)
        return store
