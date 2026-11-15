"""Plain data types mirrored from the wire protocol (proto package polign.v1)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

# MetaValue is one typed metadata scalar. Numbers are stored as numbers and
# booleans as booleans; by default the server *returns* every value as a
# string so older clients keep working — pass ``typed_metadata=True`` to
# reads to get typed values back.
MetaValue = Union[str, int, float, bool]


def _to_float_list(values: Sequence[float]) -> List[float]:
    """Coerce values (list, tuple, numpy array, ...) to a plain float list."""
    tolist = getattr(values, "tolist", None)
    if callable(tolist):  # numpy arrays and friends, without importing numpy
        values = tolist()
    return [float(v) for v in values]


@dataclass
class Vector:
    """A stored vector: id, float values, and metadata (typed scalars)."""

    id: str
    values: List[float]
    metadata: Dict[str, MetaValue] = field(default_factory=dict)


@dataclass
class Hit:
    """One search result.

    ``distance`` is the collection metric where smaller is closer. ``score``
    is the BM25/fused relevance where larger is better; it is 0.0 on a pure
    vector search.
    """

    id: str
    distance: float
    score: float = 0.0
    metadata: Dict[str, MetaValue] = field(default_factory=dict)


class HitList(List[Hit]):
    """The hits of a search: a list of :class:`Hit`, plus what a fuzzy search
    corrected.

    ``corrections`` maps each query word that ``fuzzy=True`` replaced to the
    word it searched for instead. It is empty when nothing was replaced, and
    always empty without ``fuzzy``.
    """

    corrections: Dict[str, str]

    def __init__(self, hits=(), corrections: Optional[Dict[str, str]] = None):
        super().__init__(hits)
        self.corrections = dict(corrections or {})


@dataclass
class MetadataPatch:
    """One record's patch in :meth:`Client.patch_metadata`: keys in ``set``
    are written, keys in ``unset`` removed, every other key kept."""

    id: str
    set: Optional[Dict[str, MetaValue]] = None
    unset: Optional[Sequence[str]] = None


@dataclass
class Facet:
    """A request for the counts of one metadata field.

    Without ``ranges`` the answer is one count per distinct value, highest
    first, cut to ``limit`` (0 means the server default of 100; at most
    1000). With ``ranges``, a list of ``(from, to)`` pairs where either bound
    may be ``None``, the answer is one count per range for numeric values:
    ``from`` is inclusive, ``to`` exclusive.

    A facet counts the records the request's filter matches. Give it its own
    ``filter`` to replace the request filter for this facet only, which is
    how a results page keeps showing the other brands while one is selected.
    ``own_filter=True`` with no ``filter`` counts every record. ``name`` keys
    the facet in the result and defaults to ``field``.
    """

    field: str
    name: str = ""
    limit: int = 0
    ranges: Optional[Sequence[Tuple[Optional[float], Optional[float]]]] = None
    filter: Optional[Dict[str, Any]] = None
    own_filter: bool = False


@dataclass
class FacetValue:
    """One distinct value and the number of records holding it."""

    value: MetaValue
    count: int


@dataclass
class FacetRangeCount:
    """One requested range and the number of records in it. ``start`` is the
    range's inclusive lower bound and ``end`` its exclusive upper bound;
    ``None`` is an open side."""

    start: Optional[float]
    end: Optional[float]
    count: int


@dataclass
class FacetResult:
    """The counts of one facet.

    ``matched`` is how many records the facet's filter selected and
    ``missing`` how many of those do not carry the field. ``distinct`` is the
    number of distinct values before ``limit`` cut the list; it is ``None``
    for a range facet.
    """

    field: str
    matched: int = 0
    missing: int = 0
    distinct: Optional[int] = None
    values: List[FacetValue] = field(default_factory=list)
    ranges: List[FacetRangeCount] = field(default_factory=list)


@dataclass
class FacetCounts:
    """The answer to a facets request. ``total`` is the number of records the
    request filter matches; ``facets`` maps each facet's name to its counts."""

    total: int = 0
    facets: Dict[str, FacetResult] = field(default_factory=dict)

    def __getitem__(self, name: str) -> FacetResult:
        return self.facets[name]


@dataclass
class Fusion:
    """Hybrid (vector + text) fusion configuration.

    method: "rrf" (default) or "linear".
    alpha:  linear only — weight of the vector leg in [0, 1], default 0.5.
    rrf_k:  rrf only — rank constant, default 60.
    """

    method: str = "rrf"
    alpha: float = 0.5
    rrf_k: int = 60


@dataclass
class VectorPage:
    """One page of :meth:`Client.list` results.

    ``total`` is the live vector count of the collection, independent of
    limit/offset. Iterating the page iterates its vectors.
    """

    vectors: List[Vector]
    total: int

    def __iter__(self):
        return iter(self.vectors)

    def __len__(self):
        return len(self.vectors)


@dataclass
class DeleteResult:
    """Ids whose delete was recorded, and whether more matches remain.

    ``truncated`` is only ever set for a filter delete: one call removes at
    most 5000 records, so a filter matching more reports what it removed and
    leaves the rest. Repeat the identical call until ``truncated`` is False.
    Explicit ids known to be absent are omitted; a cold-only collection may
    report ids optimistically when it cannot point-check persisted data.
    Iterating is len()/bool()-friendly so ``if result:`` still reads naturally.
    """

    ids: List[str]
    truncated: bool = False

    def __iter__(self):
        return iter(self.ids)

    def __len__(self) -> int:
        return len(self.ids)


@dataclass
class CollectionDescription:
    """Query-facing shape and index capabilities of a collection."""

    name: str
    dimension: int
    metric: str
    index_type: str
    segment_backed: bool = False
    text_field: str = ""


@dataclass
class CollectionBackend:
    """Where a bring-your-own-bucket collection lives and how the fleet
    reaches it. ``external_id`` is server-assigned — leave it empty on
    create; the minted value comes back in :class:`CollectionInfo` for your
    role's trust policy."""

    uri: str
    role_arn: str = ""
    external_id: str = ""
    region: str = ""
    gcs_service_account: str = ""


@dataclass
class CollectionInfo:
    """A registered collection. ``claim_token`` is set only on the create
    response and never shown again; ``status`` is one of "pending",
    "active", "disabled", "degraded"."""

    name: str
    status: str
    backend: CollectionBackend
    backend_id: str = ""
    verified_capabilities: List[str] = field(default_factory=list)
    created_at: str = ""
    verified_at: str = ""
    claim_token: str = ""
    claim_path: str = ""
    message: str = ""
    warnings: List[str] = field(default_factory=list)
