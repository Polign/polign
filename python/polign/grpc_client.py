"""gRPC client for polign_db. Requires the ``grpc`` extra: pip install polign[grpc].

Same operations and semantics as the HTTP :class:`polign.Client`, over
the server's gRPC listener (default :23001). Wire-compatible with the Go
client (proto package polign.v1).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from . import errors
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

try:
    import grpc
except ImportError as _e:  # pragma: no cover
    raise ImportError(
        "polign.GrpcClient requires grpcio; install it with: pip install polign[grpc]"
    ) from _e

from ._filter import filter_expr_from_dict
from ._pb import vectordb_pb2 as pb
from ._pb import vectordb_pb2_grpc as pb_grpc



class GrpcClient:
    """A thin client for a polign_db server over gRPC.

    >>> c = GrpcClient("localhost:23001")
    >>> c.put("docs", "doc-1", embedding, metadata={"title": "Cats"})

    Plaintext by default, matching the Go client; pass
    ``credentials=grpc.ssl_channel_credentials(...)`` for TLS. Safe to share
    across threads.
    """

    def __init__(
        self,
        address: str = "localhost:23001",
        *,
        api_key: Optional[str] = None,
        timeout: float = 30.0,
        credentials: Optional["grpc.ChannelCredentials"] = None,
        channel_options: Optional[list] = None,
    ):
        if credentials is not None:
            self._channel = grpc.secure_channel(address, credentials, options=channel_options)
        else:
            self._channel = grpc.insecure_channel(address, options=channel_options)
        self._stub = pb_grpc.VectorDBStub(self._channel)
        self._timeout = timeout
        self._metadata = []
        if api_key:
            self._metadata.append(("authorization", "Bearer " + api_key))

    # -- operations ---------------------------------------------------------

    def put(
        self,
        collection: str,
        id: str,
        values: Sequence[float],
        metadata: Optional[Dict[str, MetaValue]] = None,
    ) -> str:
        """Insert or replace (upsert) a vector. Returns the stored id."""
        req = pb.PutVectorRequest(
            collection=collection,
            vector=_vector_to_pb(id, values, metadata),
        )
        return self._call(self._stub.PutVector, req).id

    def put_many(self, collection: str, vectors: Sequence[Vector]) -> List[str]:
        """Insert or replace a batch of vectors in one RPC.

        Same semantics as the HTTP client: the whole batch is validated before
        anything is applied (id, non-empty values, uniform dimension, at most
        5000 vectors per batch — chunk larger loads); on a mid-batch server
        failure earlier vectors remain applied. After an ambiguous failure,
        reconcile stored state before deciding whether to retry: replaying an
        old upsert can overwrite a newer concurrent update. Returns ids in
        request order.
        """
        req = pb.PutVectorsRequest(
            collection=collection,
            vectors=[_vector_to_pb(v.id, v.values, v.metadata) for v in vectors],
        )
        return list(self._call(self._stub.PutVectors, req).ids)

    def get(self, collection: str, id: str, *, typed_metadata: bool = False) -> Vector:
        """Fetch a vector by id. Raises :class:`~polign.NotFoundError` if absent.

        Values come from the server's RAM state: byte-exact on full-vector
        collections, but a compressed reconstruction on a collection whose
        vectors were flushed to object storage. Use :meth:`get_many` for the
        byte-exact contract.
        """
        req = pb.GetVectorRequest(collection=collection, id=id)
        return _vector_from_pb(self._call(self._stub.GetVector, req).vector, typed_metadata)

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
        req = pb.GetVectorsRequest(collection=collection, ids=list(ids))
        return [
            _vector_from_pb(v, typed_metadata)
            for v in self._call(self._stub.GetVectors, req).vectors
        ]

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

        ``filter`` restricts the listing by metadata with the same dict
        language as :meth:`search`; ``offset`` and the page's ``total`` then
        count matching vectors only.
        """
        req = pb.ListVectorsRequest(
            collection=collection,
            limit=limit,
            offset=offset,
            filter_expr=filter_expr_from_dict(filter),
        )
        resp = self._call(self._stub.ListVectors, req)
        return VectorPage(
            vectors=[_vector_from_pb(v, typed_metadata) for v in resp.vectors],
            total=resp.total,
        )

    def delete(self, collection: str, id: str) -> bool:
        """Delete a vector and report whether its delete was recorded.

        Returns False for an id known to be absent. A cold-only collection may
        report True optimistically because it cannot point-check persisted ids;
        deleting an actually absent id remains a no-op during replay.
        """
        req = pb.DeleteVectorRequest(collection=collection, id=id)
        return self._call(self._stub.DeleteVector, req).deleted

    def update_metadata(
        self,
        collection: str,
        ids: Sequence[str],
        *,
        set: Optional[Dict[str, Any]] = None,
        unset: Optional[Sequence[str]] = None,
    ) -> List[str]:
        """Apply one metadata patch to a batch of records; see the HTTP
        client's update_metadata for the contract."""
        req = pb.UpdateMetadataRequest(collection=collection)
        req.ids.extend(ids)
        if set:
            for k, v in set.items():
                req.set[k].CopyFrom(_value_to_pb(k, v, in_list=False))
        if unset:
            req.unset.extend(unset)
        resp = self._call(self._stub.UpdateMetadata, req)
        return list(resp.ids)

    def delete_many(
        self,
        collection: str,
        ids: Optional[Sequence[str]] = None,
        *,
        filter: Optional[Dict[str, Any]] = None,
    ) -> DeleteResult:
        """Delete by ids or metadata filter and return the recorded ids.

        Known-missing explicit ids are omitted. A cold-only collection may
        report requested ids optimistically when it cannot point-check
        persisted ids. A filter matching more than one batch removes what it
        found and sets ``truncated``; repeat the identical call until it is
        False.
        """
        req = pb.DeleteVectorsRequest(collection=collection)
        if ids is not None:
            req.ids.extend(ids)
        if filter is not None:
            expr = filter_expr_from_dict(filter)
            if expr is None:
                raise errors.InvalidArgumentError(
                    "filter must contain at least one condition"
                )
            req.filter_expr.CopyFrom(expr)
        resp = self._call(self._stub.DeleteVectors, req)
        return DeleteResult(ids=list(resp.ids), truncated=resp.truncated)

    def describe_collection(self, collection: str) -> CollectionDescription:
        """Return dimension, metric, index type, and segment capabilities."""
        resp = self._call(
            self._stub.DescribeCollection,
            pb.DescribeCollectionRequest(collection=collection),
        )
        return CollectionDescription(
            name=resp.name,
            dimension=resp.dimension,
            metric=resp.metric,
            index_type=resp.index_type,
            segment_backed=resp.segment_backed,
            text_field=resp.text_field,
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
        """Nearest-neighbour / text / hybrid search (same semantics as HTTP).

        ``filter`` takes the same dict language as the HTTP client:
        ``{"key": "value"}`` is equality (AND across
        keys); per-key operator objects (``$eq``, ``$ne``, ``$in``, ``$gt``,
        ``$gte``, ``$lt``, ``$lte``, ``$exists``) and the composers
        ``$and``/``$or``/``$not`` express richer predicates. It is converted
        client-side to the wire's typed filter expression. ``rescore`` tunes
        the exact-rescore stage on a compressed (IVF-PQ) collection: 0 =
        server default, > 0 = pool size, < 0 = ADC-only (the approximate
        fast tier).
        """
        req = pb.SearchVectorsRequest(
            collection=collection,
            values=_to_float_list(values) if values is not None else [],
            k=k,
            ef=ef,
            cold=cold,
            nprobe=nprobe,
            filter_expr=filter_expr_from_dict(filter),
            text=text or "",
            rescore=rescore,
        )
        if fusion is not None:
            req.fusion.method = fusion.method
            req.fusion.alpha = fusion.alpha
            req.fusion.rrf_k = fusion.rrf_k
        resp = self._call(self._stub.SearchVectors, req)
        return [
            Hit(
                id=h.id,
                distance=h.distance,
                score=h.score,
                metadata=_metadata_from_pb(h, typed_metadata),
            )
            for h in resp.hits
        ]

    # -- collection admin (bring-your-own-bucket; server needs -byo-store) --

    def create_collection(self, name: str, backend: CollectionBackend) -> CollectionInfo:
        """Register a collection on a customer-owned backend (see the HTTP
        client's ``create_collection`` for the lifecycle)."""
        req = pb.CreateCollectionRequest(
            collection=name, backend=_backend_to_pb(backend)
        )
        resp = self._admin_call(self._stub.CreateCollection, req)
        return _collection_from_pb(resp.info)

    def get_collection(self, name: str) -> CollectionInfo:
        """Describe a registered collection."""
        resp = self._admin_call(
            self._stub.GetCollection, pb.GetCollectionRequest(collection=name)
        )
        return _collection_from_pb(resp.info)

    def list_collections(self) -> List[CollectionInfo]:
        """List the server's registered collections."""
        resp = self._admin_call(
            self._stub.ListCollections, pb.ListCollectionsRequest()
        )
        return [_collection_from_pb(c) for c in resp.collections]

    def delete_collection(self, name: str) -> bool:
        """Permanently disable and reserve a collection name.

        Data in the customer bucket is untouched.
        """
        resp = self._admin_call(
            self._stub.DeleteCollection, pb.DeleteCollectionRequest(collection=name)
        )
        return bool(resp.deleted)

    def verify_collection(self, name: str) -> CollectionInfo:
        """Run bucket verification now instead of waiting for the automatic
        retry. Idempotent on an active collection."""
        resp = self._admin_call(
            self._stub.VerifyCollection, pb.VerifyCollectionRequest(collection=name)
        )
        return _collection_from_pb(resp.info)

    def close(self) -> None:
        """Close the underlying channel. The client is unusable afterwards."""
        self._channel.close()

    def __enter__(self) -> "GrpcClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- transport ----------------------------------------------------------

    def _call(self, method, request):
        try:
            return method(request, timeout=self._timeout, metadata=self._metadata)
        except grpc.RpcError as e:
            raise _map_rpc_error(e) from e

    def _admin_call(self, method, request):
        try:
            return method(request, timeout=self._timeout, metadata=self._metadata)
        except grpc.RpcError as e:
            raise _map_admin_rpc_error(e) from e


def _map_rpc_error(e: "grpc.RpcError") -> errors.PolignError:
    code = e.code()
    message = e.details() or str(code)
    if code == grpc.StatusCode.NOT_FOUND:
        return errors.NotFoundError(message)
    if code == grpc.StatusCode.INVALID_ARGUMENT:
        return errors.InvalidArgumentError(message)
    if code == grpc.StatusCode.UNIMPLEMENTED:
        return errors.NotEnabledError(message)
    if code == grpc.StatusCode.UNAUTHENTICATED:
        return errors.AuthenticationError(message)
    if code == grpc.StatusCode.PERMISSION_DENIED:
        return errors.PermissionDeniedError(message)
    if code == grpc.StatusCode.RESOURCE_EXHAUSTED:
        return errors.RateLimitError(message)
    if code == grpc.StatusCode.FAILED_PRECONDITION:
        # Fleet mode: the message names the owning node.
        return errors.NotOwnerError(message)
    if code == grpc.StatusCode.UNAVAILABLE:
        # gRPC uses UNAVAILABLE for both server-declared degradation and
        # transport outages, and exposes no reliable discriminator.
        return errors.UnavailableError(message)
    return errors.ServerError(message)


def _vector_from_pb(v, typed_metadata=False) -> Vector:
    return Vector(
        id=v.id, values=list(v.values), metadata=_metadata_from_pb(v, typed_metadata)
    )


def _vector_to_pb(id, values, metadata):
    legacy, typed = _metadata_to_pb(metadata)
    return pb.Vector(
        id=id, values=_to_float_list(values), metadata=legacy, typed_metadata=typed
    )


def _metadata_to_pb(metadata):
    """Split metadata onto the wire's two fields: all-string maps use the
    legacy field (works with every server); any typed value moves the map to
    ``typed_metadata`` (needs a server with typed-metadata support)."""
    if not metadata:
        return {}, {}
    if all(isinstance(v, str) for v in metadata.values()):
        return dict(metadata), {}
    typed = {}
    for k, v in metadata.items():
        typed[k] = _value_to_pb(k, v, in_list=False)
    return {}, typed


def _value_to_pb(key, v, in_list):
    """One metadata value to its wire form. Lists hold scalars only."""
    if isinstance(v, bool):  # bool before int: bool is an int subclass
        return pb.TypedValue(boolean=v)
    if isinstance(v, str):
        return pb.TypedValue(str=v)
    if isinstance(v, (int, float)):
        return pb.TypedValue(number=float(v))
    if isinstance(v, (list, tuple)):
        if in_list:
            raise TypeError(f"metadata {key!r}: lists cannot contain lists")
        elems = [_value_to_pb(key, e, in_list=True) for e in v]
        return pb.TypedValue(list=pb.TypedValueList(values=elems))
    raise TypeError(
        f"metadata {key!r} must be a string, number, bool, or a list of those,"
        f" got {type(v).__name__}"
    )


def _metadata_from_pb(msg, typed_metadata=False):
    """Resolve response metadata. By default the legacy string field is used
    (the server always fills it with every value's canonical string form, so
    behavior matches the HTTP client and pre-typed servers); pass
    ``typed_metadata=True`` to read the typed field when the server sent it."""
    typed = getattr(msg, "typed_metadata", None)
    if typed_metadata and typed:
        return {k: _value_from_pb(tv) for k, tv in typed.items()}
    return dict(msg.metadata)


def _value_from_pb(tv):
    which = tv.WhichOneof("value")
    if which == "number":
        return tv.number
    if which == "boolean":
        return tv.boolean
    if which == "list":
        return [_value_from_pb(e) for e in tv.list.values]
    return tv.str


def _map_admin_rpc_error(e: "grpc.RpcError") -> errors.PolignError:
    """Collection-admin mapping: FAILED_PRECONDITION means "pending", not
    "wrong node", and UNIMPLEMENTED means the feature is off."""
    code = e.code()
    message = e.details() or str(code)
    if code == grpc.StatusCode.UNIMPLEMENTED:
        return errors.NotEnabledError(message)
    if code == grpc.StatusCode.ALREADY_EXISTS:
        return errors.ConflictError(message)
    if code == grpc.StatusCode.FAILED_PRECONDITION:
        return errors.ConflictError(message)
    if code == grpc.StatusCode.UNAVAILABLE:
        return errors.UnavailableError(message)
    return _map_rpc_error(e)


def _backend_to_pb(b: CollectionBackend):
    return pb.CollectionBackend(
        uri=b.uri,
        role_arn=b.role_arn,
        external_id=b.external_id,
        region=b.region,
        gcs_service_account=b.gcs_service_account,
    )


def _collection_from_pb(info) -> CollectionInfo:
    b = info.backend
    return CollectionInfo(
        name=info.name,
        status=info.status,
        backend=CollectionBackend(
            uri=b.uri,
            role_arn=b.role_arn,
            external_id=b.external_id,
            region=b.region,
            gcs_service_account=b.gcs_service_account,
        ),
        backend_id=info.backend_id,
        verified_capabilities=list(info.verified_capabilities),
        created_at=str(info.created_at_unix or ""),
        verified_at=str(info.verified_at_unix or ""),
        claim_token=info.claim_token,
        claim_path=info.claim_path,
        warnings=list(info.warnings),
    )
