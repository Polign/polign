from google.protobuf.internal import containers as _containers
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class CollectionBackend(_message.Message):
    __slots__ = ("uri", "role_arn", "external_id", "region", "gcs_service_account")
    URI_FIELD_NUMBER: _ClassVar[int]
    ROLE_ARN_FIELD_NUMBER: _ClassVar[int]
    EXTERNAL_ID_FIELD_NUMBER: _ClassVar[int]
    REGION_FIELD_NUMBER: _ClassVar[int]
    GCS_SERVICE_ACCOUNT_FIELD_NUMBER: _ClassVar[int]
    uri: str
    role_arn: str
    external_id: str
    region: str
    gcs_service_account: str
    def __init__(self, uri: _Optional[str] = ..., role_arn: _Optional[str] = ..., external_id: _Optional[str] = ..., region: _Optional[str] = ..., gcs_service_account: _Optional[str] = ...) -> None: ...

class CollectionInfo(_message.Message):
    __slots__ = ("name", "status", "backend", "backend_id", "verified_capabilities", "created_at_unix", "verified_at_unix", "claim_token", "claim_path", "warnings")
    NAME_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    BACKEND_FIELD_NUMBER: _ClassVar[int]
    BACKEND_ID_FIELD_NUMBER: _ClassVar[int]
    VERIFIED_CAPABILITIES_FIELD_NUMBER: _ClassVar[int]
    CREATED_AT_UNIX_FIELD_NUMBER: _ClassVar[int]
    VERIFIED_AT_UNIX_FIELD_NUMBER: _ClassVar[int]
    CLAIM_TOKEN_FIELD_NUMBER: _ClassVar[int]
    CLAIM_PATH_FIELD_NUMBER: _ClassVar[int]
    WARNINGS_FIELD_NUMBER: _ClassVar[int]
    name: str
    status: str
    backend: CollectionBackend
    backend_id: str
    verified_capabilities: _containers.RepeatedScalarFieldContainer[str]
    created_at_unix: int
    verified_at_unix: int
    claim_token: str
    claim_path: str
    warnings: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, name: _Optional[str] = ..., status: _Optional[str] = ..., backend: _Optional[_Union[CollectionBackend, _Mapping]] = ..., backend_id: _Optional[str] = ..., verified_capabilities: _Optional[_Iterable[str]] = ..., created_at_unix: _Optional[int] = ..., verified_at_unix: _Optional[int] = ..., claim_token: _Optional[str] = ..., claim_path: _Optional[str] = ..., warnings: _Optional[_Iterable[str]] = ...) -> None: ...

class CreateCollectionRequest(_message.Message):
    __slots__ = ("collection", "backend")
    COLLECTION_FIELD_NUMBER: _ClassVar[int]
    BACKEND_FIELD_NUMBER: _ClassVar[int]
    collection: str
    backend: CollectionBackend
    def __init__(self, collection: _Optional[str] = ..., backend: _Optional[_Union[CollectionBackend, _Mapping]] = ...) -> None: ...

class CreateCollectionResponse(_message.Message):
    __slots__ = ("info",)
    INFO_FIELD_NUMBER: _ClassVar[int]
    info: CollectionInfo
    def __init__(self, info: _Optional[_Union[CollectionInfo, _Mapping]] = ...) -> None: ...

class GetCollectionRequest(_message.Message):
    __slots__ = ("collection",)
    COLLECTION_FIELD_NUMBER: _ClassVar[int]
    collection: str
    def __init__(self, collection: _Optional[str] = ...) -> None: ...

class GetCollectionResponse(_message.Message):
    __slots__ = ("info",)
    INFO_FIELD_NUMBER: _ClassVar[int]
    info: CollectionInfo
    def __init__(self, info: _Optional[_Union[CollectionInfo, _Mapping]] = ...) -> None: ...

class ListCollectionsRequest(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class ListCollectionsResponse(_message.Message):
    __slots__ = ("collections",)
    COLLECTIONS_FIELD_NUMBER: _ClassVar[int]
    collections: _containers.RepeatedCompositeFieldContainer[CollectionInfo]
    def __init__(self, collections: _Optional[_Iterable[_Union[CollectionInfo, _Mapping]]] = ...) -> None: ...

class DeleteCollectionRequest(_message.Message):
    __slots__ = ("collection",)
    COLLECTION_FIELD_NUMBER: _ClassVar[int]
    collection: str
    def __init__(self, collection: _Optional[str] = ...) -> None: ...

class DeleteCollectionResponse(_message.Message):
    __slots__ = ("deleted",)
    DELETED_FIELD_NUMBER: _ClassVar[int]
    deleted: bool
    def __init__(self, deleted: bool = ...) -> None: ...

class VerifyCollectionRequest(_message.Message):
    __slots__ = ("collection",)
    COLLECTION_FIELD_NUMBER: _ClassVar[int]
    collection: str
    def __init__(self, collection: _Optional[str] = ...) -> None: ...

class VerifyCollectionResponse(_message.Message):
    __slots__ = ("info",)
    INFO_FIELD_NUMBER: _ClassVar[int]
    info: CollectionInfo
    def __init__(self, info: _Optional[_Union[CollectionInfo, _Mapping]] = ...) -> None: ...

class TypedValue(_message.Message):
    __slots__ = ("str", "number", "boolean", "list")
    STR_FIELD_NUMBER: _ClassVar[int]
    NUMBER_FIELD_NUMBER: _ClassVar[int]
    BOOLEAN_FIELD_NUMBER: _ClassVar[int]
    LIST_FIELD_NUMBER: _ClassVar[int]
    str: str
    number: float
    boolean: bool
    list: TypedValueList
    def __init__(self, str: _Optional[str] = ..., number: _Optional[float] = ..., boolean: bool = ..., list: _Optional[_Union[TypedValueList, _Mapping]] = ...) -> None: ...

class Vector(_message.Message):
    __slots__ = ("id", "values", "metadata", "typed_metadata")
    class MetadataEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: str
        def __init__(self, key: _Optional[str] = ..., value: _Optional[str] = ...) -> None: ...
    class TypedMetadataEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: TypedValue
        def __init__(self, key: _Optional[str] = ..., value: _Optional[_Union[TypedValue, _Mapping]] = ...) -> None: ...
    ID_FIELD_NUMBER: _ClassVar[int]
    VALUES_FIELD_NUMBER: _ClassVar[int]
    METADATA_FIELD_NUMBER: _ClassVar[int]
    TYPED_METADATA_FIELD_NUMBER: _ClassVar[int]
    id: str
    values: _containers.RepeatedScalarFieldContainer[float]
    metadata: _containers.ScalarMap[str, str]
    typed_metadata: _containers.MessageMap[str, TypedValue]
    def __init__(self, id: _Optional[str] = ..., values: _Optional[_Iterable[float]] = ..., metadata: _Optional[_Mapping[str, str]] = ..., typed_metadata: _Optional[_Mapping[str, TypedValue]] = ...) -> None: ...

class PutVectorRequest(_message.Message):
    __slots__ = ("collection", "vector")
    COLLECTION_FIELD_NUMBER: _ClassVar[int]
    VECTOR_FIELD_NUMBER: _ClassVar[int]
    collection: str
    vector: Vector
    def __init__(self, collection: _Optional[str] = ..., vector: _Optional[_Union[Vector, _Mapping]] = ...) -> None: ...

class PutVectorResponse(_message.Message):
    __slots__ = ("id",)
    ID_FIELD_NUMBER: _ClassVar[int]
    id: str
    def __init__(self, id: _Optional[str] = ...) -> None: ...

class PutVectorsRequest(_message.Message):
    __slots__ = ("collection", "vectors")
    COLLECTION_FIELD_NUMBER: _ClassVar[int]
    VECTORS_FIELD_NUMBER: _ClassVar[int]
    collection: str
    vectors: _containers.RepeatedCompositeFieldContainer[Vector]
    def __init__(self, collection: _Optional[str] = ..., vectors: _Optional[_Iterable[_Union[Vector, _Mapping]]] = ...) -> None: ...

class PutVectorsResponse(_message.Message):
    __slots__ = ("ids",)
    IDS_FIELD_NUMBER: _ClassVar[int]
    ids: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, ids: _Optional[_Iterable[str]] = ...) -> None: ...

class GetVectorRequest(_message.Message):
    __slots__ = ("collection", "id")
    COLLECTION_FIELD_NUMBER: _ClassVar[int]
    ID_FIELD_NUMBER: _ClassVar[int]
    collection: str
    id: str
    def __init__(self, collection: _Optional[str] = ..., id: _Optional[str] = ...) -> None: ...

class GetVectorResponse(_message.Message):
    __slots__ = ("vector",)
    VECTOR_FIELD_NUMBER: _ClassVar[int]
    vector: Vector
    def __init__(self, vector: _Optional[_Union[Vector, _Mapping]] = ...) -> None: ...

class GetVectorsRequest(_message.Message):
    __slots__ = ("collection", "ids")
    COLLECTION_FIELD_NUMBER: _ClassVar[int]
    IDS_FIELD_NUMBER: _ClassVar[int]
    collection: str
    ids: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, collection: _Optional[str] = ..., ids: _Optional[_Iterable[str]] = ...) -> None: ...

class GetVectorsResponse(_message.Message):
    __slots__ = ("vectors",)
    VECTORS_FIELD_NUMBER: _ClassVar[int]
    vectors: _containers.RepeatedCompositeFieldContainer[Vector]
    def __init__(self, vectors: _Optional[_Iterable[_Union[Vector, _Mapping]]] = ...) -> None: ...

class ListVectorsRequest(_message.Message):
    __slots__ = ("collection", "limit", "offset", "filter_expr")
    COLLECTION_FIELD_NUMBER: _ClassVar[int]
    LIMIT_FIELD_NUMBER: _ClassVar[int]
    OFFSET_FIELD_NUMBER: _ClassVar[int]
    FILTER_EXPR_FIELD_NUMBER: _ClassVar[int]
    collection: str
    limit: int
    offset: int
    filter_expr: FilterExpr
    def __init__(self, collection: _Optional[str] = ..., limit: _Optional[int] = ..., offset: _Optional[int] = ..., filter_expr: _Optional[_Union[FilterExpr, _Mapping]] = ...) -> None: ...

class ListVectorsResponse(_message.Message):
    __slots__ = ("vectors", "total")
    VECTORS_FIELD_NUMBER: _ClassVar[int]
    TOTAL_FIELD_NUMBER: _ClassVar[int]
    vectors: _containers.RepeatedCompositeFieldContainer[Vector]
    total: int
    def __init__(self, vectors: _Optional[_Iterable[_Union[Vector, _Mapping]]] = ..., total: _Optional[int] = ...) -> None: ...

class DeleteVectorRequest(_message.Message):
    __slots__ = ("collection", "id")
    COLLECTION_FIELD_NUMBER: _ClassVar[int]
    ID_FIELD_NUMBER: _ClassVar[int]
    collection: str
    id: str
    def __init__(self, collection: _Optional[str] = ..., id: _Optional[str] = ...) -> None: ...

class DeleteVectorResponse(_message.Message):
    __slots__ = ("deleted",)
    DELETED_FIELD_NUMBER: _ClassVar[int]
    deleted: bool
    def __init__(self, deleted: bool = ...) -> None: ...

class UpdateMetadataRequest(_message.Message):
    __slots__ = ("collection", "ids", "set", "unset")
    class SetEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: TypedValue
        def __init__(self, key: _Optional[str] = ..., value: _Optional[_Union[TypedValue, _Mapping]] = ...) -> None: ...
    COLLECTION_FIELD_NUMBER: _ClassVar[int]
    IDS_FIELD_NUMBER: _ClassVar[int]
    SET_FIELD_NUMBER: _ClassVar[int]
    UNSET_FIELD_NUMBER: _ClassVar[int]
    collection: str
    ids: _containers.RepeatedScalarFieldContainer[str]
    set: _containers.MessageMap[str, TypedValue]
    unset: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, collection: _Optional[str] = ..., ids: _Optional[_Iterable[str]] = ..., set: _Optional[_Mapping[str, TypedValue]] = ..., unset: _Optional[_Iterable[str]] = ...) -> None: ...

class UpdateMetadataResponse(_message.Message):
    __slots__ = ("ids",)
    IDS_FIELD_NUMBER: _ClassVar[int]
    ids: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, ids: _Optional[_Iterable[str]] = ...) -> None: ...

class DeleteVectorsRequest(_message.Message):
    __slots__ = ("collection", "ids", "filter_expr")
    COLLECTION_FIELD_NUMBER: _ClassVar[int]
    IDS_FIELD_NUMBER: _ClassVar[int]
    FILTER_EXPR_FIELD_NUMBER: _ClassVar[int]
    collection: str
    ids: _containers.RepeatedScalarFieldContainer[str]
    filter_expr: FilterExpr
    def __init__(self, collection: _Optional[str] = ..., ids: _Optional[_Iterable[str]] = ..., filter_expr: _Optional[_Union[FilterExpr, _Mapping]] = ...) -> None: ...

class DeleteVectorsResponse(_message.Message):
    __slots__ = ("ids", "truncated")
    IDS_FIELD_NUMBER: _ClassVar[int]
    TRUNCATED_FIELD_NUMBER: _ClassVar[int]
    ids: _containers.RepeatedScalarFieldContainer[str]
    truncated: bool
    def __init__(self, ids: _Optional[_Iterable[str]] = ..., truncated: bool = ...) -> None: ...

class DescribeCollectionRequest(_message.Message):
    __slots__ = ("collection",)
    COLLECTION_FIELD_NUMBER: _ClassVar[int]
    collection: str
    def __init__(self, collection: _Optional[str] = ...) -> None: ...

class DescribeCollectionResponse(_message.Message):
    __slots__ = ("name", "dimension", "metric", "index_type", "segment_backed", "text_field")
    NAME_FIELD_NUMBER: _ClassVar[int]
    DIMENSION_FIELD_NUMBER: _ClassVar[int]
    METRIC_FIELD_NUMBER: _ClassVar[int]
    INDEX_TYPE_FIELD_NUMBER: _ClassVar[int]
    SEGMENT_BACKED_FIELD_NUMBER: _ClassVar[int]
    TEXT_FIELD_FIELD_NUMBER: _ClassVar[int]
    name: str
    dimension: int
    metric: str
    index_type: str
    segment_backed: bool
    text_field: str
    def __init__(self, name: _Optional[str] = ..., dimension: _Optional[int] = ..., metric: _Optional[str] = ..., index_type: _Optional[str] = ..., segment_backed: bool = ..., text_field: _Optional[str] = ...) -> None: ...

class SearchVectorsRequest(_message.Message):
    __slots__ = ("collection", "values", "k", "ef", "cold", "nprobe", "filter", "text", "fusion", "filter_expr", "rescore")
    class FilterEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: str
        def __init__(self, key: _Optional[str] = ..., value: _Optional[str] = ...) -> None: ...
    COLLECTION_FIELD_NUMBER: _ClassVar[int]
    VALUES_FIELD_NUMBER: _ClassVar[int]
    K_FIELD_NUMBER: _ClassVar[int]
    EF_FIELD_NUMBER: _ClassVar[int]
    COLD_FIELD_NUMBER: _ClassVar[int]
    NPROBE_FIELD_NUMBER: _ClassVar[int]
    FILTER_FIELD_NUMBER: _ClassVar[int]
    TEXT_FIELD_NUMBER: _ClassVar[int]
    FUSION_FIELD_NUMBER: _ClassVar[int]
    FILTER_EXPR_FIELD_NUMBER: _ClassVar[int]
    RESCORE_FIELD_NUMBER: _ClassVar[int]
    collection: str
    values: _containers.RepeatedScalarFieldContainer[float]
    k: int
    ef: int
    cold: bool
    nprobe: int
    filter: _containers.ScalarMap[str, str]
    text: str
    fusion: Fusion
    filter_expr: FilterExpr
    rescore: int
    def __init__(self, collection: _Optional[str] = ..., values: _Optional[_Iterable[float]] = ..., k: _Optional[int] = ..., ef: _Optional[int] = ..., cold: bool = ..., nprobe: _Optional[int] = ..., filter: _Optional[_Mapping[str, str]] = ..., text: _Optional[str] = ..., fusion: _Optional[_Union[Fusion, _Mapping]] = ..., filter_expr: _Optional[_Union[FilterExpr, _Mapping]] = ..., rescore: _Optional[int] = ...) -> None: ...

class FilterExpr(_message.Message):
    __slots__ = ("cond",)
    AND_FIELD_NUMBER: _ClassVar[int]
    OR_FIELD_NUMBER: _ClassVar[int]
    NOT_FIELD_NUMBER: _ClassVar[int]
    COND_FIELD_NUMBER: _ClassVar[int]
    cond: FilterCond
    def __init__(self, cond: _Optional[_Union[FilterCond, _Mapping]] = ..., **kwargs) -> None: ...

class FilterJunction(_message.Message):
    __slots__ = ("exprs",)
    EXPRS_FIELD_NUMBER: _ClassVar[int]
    exprs: _containers.RepeatedCompositeFieldContainer[FilterExpr]
    def __init__(self, exprs: _Optional[_Iterable[_Union[FilterExpr, _Mapping]]] = ...) -> None: ...

class FilterCond(_message.Message):
    __slots__ = ("key", "eq", "range", "exists", "typed_eq", "typed_in")
    KEY_FIELD_NUMBER: _ClassVar[int]
    EQ_FIELD_NUMBER: _ClassVar[int]
    IN_FIELD_NUMBER: _ClassVar[int]
    RANGE_FIELD_NUMBER: _ClassVar[int]
    EXISTS_FIELD_NUMBER: _ClassVar[int]
    TYPED_EQ_FIELD_NUMBER: _ClassVar[int]
    TYPED_IN_FIELD_NUMBER: _ClassVar[int]
    key: str
    eq: str
    range: FilterRange
    exists: bool
    typed_eq: TypedValue
    typed_in: TypedValueList
    def __init__(self, key: _Optional[str] = ..., eq: _Optional[str] = ..., range: _Optional[_Union[FilterRange, _Mapping]] = ..., exists: bool = ..., typed_eq: _Optional[_Union[TypedValue, _Mapping]] = ..., typed_in: _Optional[_Union[TypedValueList, _Mapping]] = ..., **kwargs) -> None: ...

class ValueList(_message.Message):
    __slots__ = ("values",)
    VALUES_FIELD_NUMBER: _ClassVar[int]
    values: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, values: _Optional[_Iterable[str]] = ...) -> None: ...

class TypedValueList(_message.Message):
    __slots__ = ("values",)
    VALUES_FIELD_NUMBER: _ClassVar[int]
    values: _containers.RepeatedCompositeFieldContainer[TypedValue]
    def __init__(self, values: _Optional[_Iterable[_Union[TypedValue, _Mapping]]] = ...) -> None: ...

class FilterRange(_message.Message):
    __slots__ = ("gt", "gte", "lt", "lte", "numeric")
    GT_FIELD_NUMBER: _ClassVar[int]
    GTE_FIELD_NUMBER: _ClassVar[int]
    LT_FIELD_NUMBER: _ClassVar[int]
    LTE_FIELD_NUMBER: _ClassVar[int]
    NUMERIC_FIELD_NUMBER: _ClassVar[int]
    gt: str
    gte: str
    lt: str
    lte: str
    numeric: bool
    def __init__(self, gt: _Optional[str] = ..., gte: _Optional[str] = ..., lt: _Optional[str] = ..., lte: _Optional[str] = ..., numeric: bool = ...) -> None: ...

class Fusion(_message.Message):
    __slots__ = ("method", "alpha", "rrf_k")
    METHOD_FIELD_NUMBER: _ClassVar[int]
    ALPHA_FIELD_NUMBER: _ClassVar[int]
    RRF_K_FIELD_NUMBER: _ClassVar[int]
    method: str
    alpha: float
    rrf_k: int
    def __init__(self, method: _Optional[str] = ..., alpha: _Optional[float] = ..., rrf_k: _Optional[int] = ...) -> None: ...

class SearchHit(_message.Message):
    __slots__ = ("id", "distance", "metadata", "score", "typed_metadata")
    class MetadataEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: str
        def __init__(self, key: _Optional[str] = ..., value: _Optional[str] = ...) -> None: ...
    class TypedMetadataEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: TypedValue
        def __init__(self, key: _Optional[str] = ..., value: _Optional[_Union[TypedValue, _Mapping]] = ...) -> None: ...
    ID_FIELD_NUMBER: _ClassVar[int]
    DISTANCE_FIELD_NUMBER: _ClassVar[int]
    METADATA_FIELD_NUMBER: _ClassVar[int]
    SCORE_FIELD_NUMBER: _ClassVar[int]
    TYPED_METADATA_FIELD_NUMBER: _ClassVar[int]
    id: str
    distance: float
    metadata: _containers.ScalarMap[str, str]
    score: float
    typed_metadata: _containers.MessageMap[str, TypedValue]
    def __init__(self, id: _Optional[str] = ..., distance: _Optional[float] = ..., metadata: _Optional[_Mapping[str, str]] = ..., score: _Optional[float] = ..., typed_metadata: _Optional[_Mapping[str, TypedValue]] = ...) -> None: ...

class SearchVectorsResponse(_message.Message):
    __slots__ = ("hits",)
    HITS_FIELD_NUMBER: _ClassVar[int]
    hits: _containers.RepeatedCompositeFieldContainer[SearchHit]
    def __init__(self, hits: _Optional[_Iterable[_Union[SearchHit, _Mapping]]] = ...) -> None: ...
