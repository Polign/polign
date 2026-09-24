"""HTTP client for polign_db. Zero dependencies — stdlib only.

Talks JSON to the server's HTTP listener (default :23000). Connections are
kept alive and pooled per thread. Reads that hit a stale keep-alive connection
are retried once on a fresh one; mutations are never retried automatically
because a disconnect can leave their outcome unknown.
"""

from __future__ import annotations

import http.client
import json
import ssl
import threading
from typing import Any, Dict, List, Optional, Sequence, Tuple
from urllib.parse import quote, urlsplit

from . import errors
from ._validation import validate_filter_finite
from .types import (
    CollectionBackend,
    CollectionDescription,
    CollectionInfo,
    DeleteResult,
    Fusion,
    Hit,
    MetaValue,
    Vector,
    VectorPage,
    _to_float_list,
)



class Client:
    """A thin client for a polign_db server over HTTP.

    >>> c = Client("http://localhost:23000")
    >>> c.put("docs", "doc-1", embedding, metadata={"title": "Cats"})
    >>> hits = c.search("docs", values=query_embedding, k=10)

    Collections are auto-created on first put, inferring their dimension from
    the first vector. ``api_key`` sends ``Authorization: Bearer <key>``. The
    client is safe to share across threads.
    """

    def __init__(
        self,
        url: str = "http://localhost:23000",
        *,
        api_key: Optional[str] = None,
        timeout: float = 30.0,
        ssl_context: Optional[ssl.SSLContext] = None,
    ):
        parts = urlsplit(url if "//" in url else "//" + url, scheme="http")
        if parts.scheme not in ("http", "https"):
            raise ValueError(f"unsupported URL scheme {parts.scheme!r} (use http or https)")
        if not parts.hostname:
            raise ValueError(f"no host in URL {url!r}")
        self._scheme = parts.scheme
        self._host = parts.hostname
        self._port = parts.port or (443 if parts.scheme == "https" else 80)
        self._prefix = parts.path.rstrip("/")
        self._timeout = timeout
        self._ssl_context = ssl_context
        self._headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if api_key:
            self._headers["Authorization"] = "Bearer " + api_key
        self._local = threading.local()
        self._conns_lock = threading.Lock()
        self._conns: List[http.client.HTTPConnection] = []

    # -- operations ---------------------------------------------------------

    def put(
        self,
        collection: str,
        id: str,
        values: Sequence[float],
        metadata: Optional[Dict[str, MetaValue]] = None,
    ) -> str:
        """Insert or replace (upsert) a vector. Returns the stored id.

        Metadata values may be strings, numbers, or booleans; numbers and
        booleans are stored typed (see :data:`~polign.types.MetaValue`).
        """
        body = {"values": _to_float_list(values)}
        if metadata:
            body["metadata"] = dict(metadata)
        resp = self._request(
            "PUT", f"/v1/collections/{_seg(collection)}/vectors/{_seg(id)}", body
        )
        return resp["id"]

    def put_many(self, collection: str, vectors: Sequence[Vector]) -> List[str]:
        """Insert or replace a batch of vectors in one request.

        The server validates the whole batch before applying anything: every
        vector needs an id, non-empty values, and the same dimension, and a
        batch holds at most 5000 vectors (chunk larger loads), or the call
        fails with nothing written. On a rarer mid-batch server failure,
        earlier vectors remain applied. The client does not automatically
        retry an ambiguous failure; reconcile state before deciding whether
        to retry. Returns the stored ids in request order.
        """
        body = {
            "vectors": [
                _vector_to_json(v.id, v.values, v.metadata) for v in vectors
            ]
        }
        resp = self._request(
            "POST", f"/v1/collections/{_seg(collection)}/vectors:batch", body
        )
        return resp.get("ids") or []

    def get(self, collection: str, id: str, *, typed_metadata: bool = False) -> Vector:
        """Fetch a vector by id. Raises :class:`~polign.NotFoundError` if absent.

        ``typed_metadata=True`` returns metadata values with their stored
        types (numbers, booleans); the default renders every value as a
        string, matching pre-typed servers and clients.

        Values come from the server's RAM state: byte-exact on full-vector
        collections, but a compressed reconstruction on a collection whose
        vectors were flushed to object storage. Use :meth:`get_many` for the
        byte-exact contract.
        """
        path = f"/v1/collections/{_seg(collection)}/vectors/{_seg(id)}"
        if typed_metadata:
            path += "?typed=true"
        resp = self._request("GET", path, retry_safe=True)
        return _vector_from_json(resp)

    def get_many(
        self, collection: str, ids: Sequence[str], *, typed_metadata: bool = False
    ) -> List[Vector]:
        """Fetch a batch of vectors by id with byte-exact values.

        Returned values are exactly the floats that were written, never a
        compressed reconstruction — on a cold-flushed collection the server
        reads them back from object storage, so a call can be slower than
        :meth:`get`. Unknown ids are omitted from the result (absence is not
        an error); vectors come back in request id order. A batch holds at
        most 5000 ids.
        """
        body: Dict[str, object] = {"ids": list(ids)}
        if typed_metadata:
            body["typed_metadata"] = True
        resp = self._request(
            "POST",
            f"/v1/collections/{_seg(collection)}/vectors:get",
            body,
            retry_safe=True,
        )
        return [_vector_from_json(v) for v in resp.get("vectors") or []]

    def list(
        self,
        collection: str,
        limit: int = 0,
        offset: int = 0,
        *,
        filter: Optional[Dict[str, Any]] = None,
        typed_metadata: bool = False,
    ) -> VectorPage:
        """List vectors ordered by id. ``limit=0`` means the server default.

        The returned page's ``total`` is the collection's live vector count.
        ``filter`` restricts the listing to vectors whose metadata matches; it
        takes the same dict language as :meth:`search` (plain equality
        mappings, per-key operators, ``$and``/``$or``/``$not``). With a
        filter, ``offset`` and ``total`` count matching vectors only, so
        pagination works unchanged.
        """
        path = f"/v1/collections/{_seg(collection)}/vectors?limit={int(limit)}&offset={int(offset)}"
        if filter:
            validate_filter_finite(filter)
            path += "&filter=" + quote(json.dumps(filter), safe="")
        if typed_metadata:
            path += "&typed=true"
        resp = self._request("GET", path, retry_safe=True)
        return VectorPage(
            vectors=[_vector_from_json(v) for v in resp.get("vectors") or []],
            total=resp.get("total", 0),
        )

    def delete(self, collection: str, id: str) -> bool:
        """Delete a vector and report whether its delete was recorded.

        Returns False for an id known to be absent. A cold-only collection may
        report True optimistically because it cannot point-check persisted ids;
        deleting an actually absent id remains a no-op during replay.
        """
        try:
            resp = self._request(
                "DELETE", f"/v1/collections/{_seg(collection)}/vectors/{_seg(id)}"
            )
        except errors.NotFoundError:
            return False
        return bool(resp.get("deleted"))

    def update_metadata(
        self,
        collection: str,
        ids: Sequence[str],
        *,
        set: Optional[Dict[str, Any]] = None,
        unset: Optional[Sequence[str]] = None,
    ) -> List[str]:
        """Apply one metadata patch to a batch of records.

        Keys in ``set`` are written, keys in ``unset`` removed, and every
        other key keeps its value; the stored vectors are untouched, so no
        re-embedding round trip is needed. Values follow the metadata value
        model (strings, numbers, booleans, and flat lists of those).

        Returns the ids the patch now holds for, in request order; ids that
        do not exist are omitted, so patching all of a document's chunks
        tolerates a shorter document. A record already in the requested state
        is reported without a new write. A batch holds at most 5000 ids.
        """
        body: Dict[str, object] = {"ids": list(ids)}
        if set:
            body["set"] = dict(set)
        if unset:
            body["unset"] = list(unset)
        resp = self._request(
            "POST", f"/v1/collections/{_seg(collection)}/vectors:update", body
        )
        return list(resp.get("ids") or [])

    def delete_many(
        self,
        collection: str,
        ids: Optional[Sequence[str]] = None,
        *,
        filter: Optional[Dict[str, Any]] = None,
    ) -> DeleteResult:
        """Delete by ids or metadata filter and return the recorded ids.

        Exactly one selector must be supplied; ids known to be missing are
        omitted from the response. A cold-only collection may report requested
        ids optimistically when it cannot point-check persisted ids. One call
        removes at most 5000 records. Too many explicit ids
        is an error, but a filter matching more removes a batch and sets
        ``truncated``, so repeat the same call until it is False::

            while True:
                result = client.delete_many("docs", filter={"ref_doc_id": "d1"})
                if not result.truncated:
                    break
        """
        body: Dict[str, object] = {}
        if ids is not None:
            body["ids"] = list(ids)
        if filter is not None:
            validate_filter_finite(filter)
            body["filter"] = dict(filter)
        resp = self._request(
            "POST", f"/v1/collections/{_seg(collection)}/vectors:delete", body
        )
        return DeleteResult(
            ids=resp.get("ids") or [], truncated=bool(resp.get("truncated"))
        )

    def describe_collection(self, collection: str) -> CollectionDescription:
        """Return dimension, metric, index type, and segment capabilities."""
        obj = self._request(
            "GET",
            f"/v1/collections/{_seg(collection)}/describe",
            retry_safe=True,
        )
        return CollectionDescription(
            name=obj.get("name", ""),
            dimension=int(obj.get("dimension", 0)),
            metric=obj.get("metric", ""),
            index_type=obj.get("index_type", ""),
            segment_backed=bool(obj.get("segment_backed", False)),
            text_field=obj.get("text_field", ""),
        )

    def search(
        self,
        collection: str,
        values: Optional[Sequence[float]] = None,
        k: int = 10,
        *,
        ef: int = 0,
        cold: bool = False,
        nprobe: int = 0,
        filter: Optional[Dict[str, Any]] = None,
        text: Optional[str] = None,
        fusion: Optional[Fusion] = None,
        rescore: int = 0,
        typed_metadata: bool = False,
    ) -> List[Hit]:
        """Nearest-neighbour / text / hybrid search.

        Pass ``values`` for vector search, ``text`` for BM25 lexical search
        (requires a segment index on the server), or both for hybrid search
        fused per ``fusion``. ``filter`` is a metadata predicate: a plain
        ``{"key": "value"}`` mapping is equality ANDed across keys, and
        per-key operator objects (``$eq``, ``$ne``, ``$in``, ``$gt``,
        ``$gte``, ``$lt``, ``$lte``, ``$exists``) plus the composers
        ``$and``/``$or``/``$not`` express richer predicates — e.g.
        ``{"ts": {"$gte": "2026-01-01"}, "lang": {"$in": ["en", "fr"]}}``. ``ef`` overrides the HNSW beam width (0 = server default).
        ``cold=True`` serves from object-store segments, probing ``nprobe``
        IVF cells (0 = server default). ``rescore`` tunes the exact-rescore
        stage on a compressed (IVF-PQ) collection: 0 = server default, > 0 =
        per-query rescore pool size, < 0 = ADC-only ranking (the approximate
        fast tier); other collection types rank exactly and ignore it.
        """
        body: Dict[str, object] = {"k": int(k)}
        if values is not None:
            body["values"] = _to_float_list(values)
        if ef:
            body["ef"] = int(ef)
        if cold:
            body["cold"] = True
        if nprobe:
            body["nprobe"] = int(nprobe)
        if rescore:
            body["rescore"] = int(rescore)
        if filter:
            validate_filter_finite(filter)
            body["filter"] = dict(filter)
        if typed_metadata:
            body["typed_metadata"] = True
        if text is not None:
            body["text"] = text
        if fusion is not None:
            body["fusion"] = {
                "method": fusion.method,
                "alpha": fusion.alpha,
                "rrf_k": fusion.rrf_k,
            }
        resp = self._request(
            "POST",
            f"/v1/collections/{_seg(collection)}/query",
            body,
            retry_safe=True,
        )
        return [
            Hit(
                id=h["id"],
                distance=h.get("distance", 0.0),
                score=h.get("score", 0.0),
                metadata=h.get("metadata") or {},
            )
            for h in resp.get("hits") or []
        ]

    # -- collection listing and admin (-byo-store required for lifecycle) --

    def create_collection(self, name: str, backend: CollectionBackend) -> CollectionInfo:
        """Register a collection on a customer-owned backend.

        A role-backed backend whose trust policy is already in place comes
        back ``active`` and usable immediately. Otherwise the collection is
        ``pending``: finish your side (write ``claim_token`` to
        ``claim_path`` in the bucket, or attach the trust policy to the
        role) and the server activates it automatically within ~30s.
        """
        body = {"backend": _backend_to_json(backend)}
        resp = self._request("POST", f"/v1/collections/{_seg(name)}", body)
        return _collection_from_json(resp)

    def get_collection(self, name: str) -> CollectionInfo:
        """Describe a registered collection."""
        resp = self._request(
            "GET", f"/v1/collections/{_seg(name)}", retry_safe=True
        )
        return _collection_from_json(resp)

    def list_collections(self) -> List[CollectionInfo]:
        """List collections (requires an operator key when auth is enabled).

        With ``-byo-store``, returns registered collections. Otherwise returns
        collections discovered in the store's generation/manifest metadata,
        sorted by name; a collection with only unpersisted writes appears
        after its first persist. An in-memory server lists local collections.
        Default-store entries have status ``active`` and empty registry fields
        (backend, timestamps, and verification details).
        Servers through 0.7.0 still require ``-byo-store`` for this method.
        """
        resp = self._request("GET", "/v1/collections", retry_safe=True)
        return [_collection_from_json(c) for c in resp.get("collections", [])]

    def delete_collection(self, name: str) -> bool:
        """Permanently disable and reserve a collection name.

        Data in the customer bucket is untouched.
        """
        resp = self._request("DELETE", f"/v1/collections/{_seg(name)}")
        return bool(resp.get("deleted"))

    def verify_collection(self, name: str) -> CollectionInfo:
        """Run bucket verification now instead of waiting for the automatic
        retry. Idempotent on an active collection."""
        resp = self._request("POST", f"/v1/collections/{_seg(name)}/verify")
        return _collection_from_json(resp)

    def backend_setup(self, uri: str) -> dict:
        """Fetch the paste-ready IAM recipe (trust policy, permissions,
        Terraform, CLI) for a backend URI, with the server's external id
        filled in."""
        return self._request(
            "GET", "/v1/setup?uri=" + quote(uri, safe=""), retry_safe=True
        )

    def health(self) -> bool:
        """True if the server answers GET /healthz with 200."""
        try:
            status, _, _ = self._raw_request(
                "GET", "/healthz", None, retry_safe=True
            )
        except errors.PolignError:
            return False
        return status == 200

    def close(self) -> None:
        """Close all pooled connections. The client is unusable afterwards."""
        with self._conns_lock:
            conns, self._conns = self._conns, []
        for conn in conns:
            conn.close()

    def __enter__(self) -> "Client":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- transport ----------------------------------------------------------

    def _request(
        self,
        method: str,
        path: str,
        body: Optional[dict] = None,
        *,
        retry_safe: bool = False,
    ) -> dict:
        status, data, headers = self._raw_request(
            method, path, body, retry_safe=retry_safe
        )
        if status in (200, 201):
            return json.loads(data) if data else {}
        try:
            message = json.loads(data).get("error", "")
        except (ValueError, AttributeError):
            message = data.decode("utf-8", "replace").strip()
        message = message or f"HTTP {status}"
        if status == 400:
            raise errors.InvalidArgumentError(message)
        if status == 401:
            raise errors.AuthenticationError(message)
        if status == 403:
            raise errors.PermissionDeniedError(message)
        if status == 404:
            raise errors.NotFoundError(message)
        if status == 421:
            owner = headers.get("X-Polign-Owner")
            if not owner:
                try:
                    owner = json.loads(data).get("owner")
                except ValueError:
                    owner = None
            raise errors.NotOwnerError(message, owner=owner)
        if status == 409:
            raise errors.ConflictError(message)
        if status == 429:
            raise errors.RateLimitError(message)
        if status == 501:
            raise errors.NotEnabledError(message)
        if status == 503:
            raise errors.UnavailableError(message)
        raise errors.ServerError(f"HTTP {status}: {message}")

    def _raw_request(
        self,
        method: str,
        path: str,
        body: Optional[dict],
        *,
        retry_safe: bool = False,
    ) -> Tuple[int, bytes, "http.client.HTTPMessage"]:
        payload = json.dumps(body).encode() if body is not None else None
        conn = self._conn()
        attempts = 2 if retry_safe else 1
        for attempt in range(1, attempts + 1):
            try:
                conn.request(method, self._prefix + path, body=payload, headers=self._headers)
                resp = conn.getresponse()
                return resp.status, resp.read(), resp.headers
            except (http.client.HTTPException, ConnectionError, BrokenPipeError, OSError) as e:
                # A disconnect after bytes were sent is ambiguous. Retrying a
                # mutation can overwrite a later update or repeat a delete, so
                # only callers that explicitly identify a read may retry.
                conn.close()
                conn = self._conn(fresh=True)
                if attempt == attempts:
                    detail = (
                        "request failed after one retry"
                        if retry_safe
                        else "request outcome is unknown; operation was not retried"
                    )
                    raise errors.ConnectionError(
                        f"request to {self._host}:{self._port} failed: {e}; {detail}"
                    ) from e
        raise AssertionError("unreachable")

    def _conn(self, fresh: bool = False) -> http.client.HTTPConnection:
        conn = getattr(self._local, "conn", None)
        if conn is None or fresh:
            if self._scheme == "https":
                conn = http.client.HTTPSConnection(
                    self._host,
                    self._port,
                    timeout=self._timeout,
                    context=self._ssl_context or ssl.create_default_context(),
                )
            else:
                conn = http.client.HTTPConnection(
                    self._host, self._port, timeout=self._timeout
                )
            self._local.conn = conn
            with self._conns_lock:
                self._conns.append(conn)
        return conn


def _seg(value: str) -> str:
    """Percent-encode one path segment (collection or vector id)."""
    return quote(str(value), safe="")


def _vector_to_json(id: str, values, metadata) -> dict:
    obj: Dict[str, object] = {"id": id, "values": _to_float_list(values)}
    if metadata:
        obj["metadata"] = dict(metadata)
    return obj


def _vector_from_json(obj: dict) -> Vector:
    return Vector(
        id=obj["id"],
        values=obj.get("values") or [],
        metadata=obj.get("metadata") or {},
    )


def _backend_to_json(b: CollectionBackend) -> dict:
    out: Dict[str, Any] = {"uri": b.uri}
    if b.role_arn:
        out["role_arn"] = b.role_arn
    if b.external_id:
        out["external_id"] = b.external_id
    if b.region:
        out["region"] = b.region
    if b.gcs_service_account:
        out["gcs_service_account"] = b.gcs_service_account
    return out


def _collection_from_json(obj: dict) -> CollectionInfo:
    b = obj.get("backend") or {}
    return CollectionInfo(
        name=obj.get("name", ""),
        status=obj.get("status", ""),
        backend=CollectionBackend(
            uri=b.get("uri", ""),
            role_arn=b.get("role_arn", ""),
            external_id=b.get("external_id", ""),
            region=b.get("region", ""),
            gcs_service_account=b.get("gcs_service_account", ""),
        ),
        backend_id=obj.get("backend_id", ""),
        verified_capabilities=obj.get("verified_capabilities") or [],
        created_at=obj.get("created_at", ""),
        verified_at=obj.get("verified_at") or "",
        claim_token=obj.get("claim_token", ""),
        claim_path=obj.get("claim_path", ""),
        message=obj.get("message", ""),
        warnings=obj.get("warnings") or [],
    )
