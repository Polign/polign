"""gRPC-only SDK compatibility and status-mapping tests."""

import pytest

grpc = pytest.importorskip("grpc")
pytest.importorskip("google.protobuf")

from polign import NotEnabledError, RateLimitError, UnavailableError  # noqa: E402
from polign.grpc_client import GrpcClient, _map_rpc_error  # noqa: E402


class _RpcError(grpc.RpcError):
    def __init__(self, code, details):
        self._code = code
        self._details = details

    def code(self):
        return self._code

    def details(self):
        return self._details


def _client_with_stub(stub):
    client = GrpcClient.__new__(GrpcClient)
    client._stub = stub
    client._timeout = 3.0
    client._metadata = []
    return client


class _ColdPointStub:
    def GetVector(self, _request, **_kwargs):
        raise _RpcError(
            grpc.StatusCode.UNIMPLEMENTED,
            "cold point reads require an in-memory index",
        )


def test_data_plane_unimplemented_maps_to_not_enabled():
    with pytest.raises(NotEnabledError, match="cold point"):
        _client_with_stub(_ColdPointStub()).get("docs", "a")


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        (grpc.StatusCode.RESOURCE_EXHAUSTED, RateLimitError),
        (grpc.StatusCode.UNAVAILABLE, UnavailableError),
    ],
)
def test_transient_status_mapping_matches_http_taxonomy(code, expected):
    mapped = _map_rpc_error(_RpcError(code, "transient"))
    assert isinstance(mapped, expected)


def test_list_metadata_round_trips_through_wire_form():
    """A list of scalars survives the wire conversion in both directions;
    nested lists are rejected before anything is sent."""
    from polign.grpc_client import _metadata_to_pb, _value_from_pb

    legacy, typed = _metadata_to_pb(
        {"acl": ["group:eng", "user:a@b.c"], "scores": [3, 9.5], "flags": [True]}
    )
    assert legacy == {}
    assert _value_from_pb(typed["acl"]) == ["group:eng", "user:a@b.c"]
    assert _value_from_pb(typed["scores"]) == [3.0, 9.5]
    assert _value_from_pb(typed["flags"]) == [True]

    with pytest.raises(TypeError, match="lists cannot contain lists"):
        _metadata_to_pb({"k": [["nested"]]})
    with pytest.raises(TypeError, match="string, number, bool"):
        _metadata_to_pb({"k": [None]})


class _RetailStub:
    """Records the request of each retail-round call and answers a canned response."""

    def __init__(self):
        self.requests = {}

    def Facets(self, request, **_kwargs):
        from polign._pb import vectordb_pb2 as pb

        self.requests["facets"] = request
        resp = pb.FacetsResponse(total=2)
        brand = resp.facets.add(name="brand", field="brand", matched=2, distinct=1)
        brand.values.add(value=pb.TypedValue(str="acme"), count=2)
        price = resp.facets.add(name="price", field="price", matched=2, missing=1)
        price.ranges.add(to=500, count=1)
        high = price.ranges.add(count=0)
        setattr(high, "from", 500)
        return resp

    def UpdateMetadata(self, request, **_kwargs):
        from polign._pb import vectordb_pb2 as pb

        self.requests["update"] = request
        return pb.UpdateMetadataResponse(ids=["p1", "p2"])

    def SearchVectors(self, request, **_kwargs):
        from polign._pb import vectordb_pb2 as pb

        self.requests["search"] = request
        resp = pb.SearchVectorsResponse()
        resp.hits.add(id="tv", score=1.5)
        resp.corrections["telvison"] = "television"
        return resp


def test_grpc_facets_patch_and_fuzzy_match_the_http_client():
    import polign

    stub = _RetailStub()
    client = _client_with_stub(stub)

    counts = client.facets(
        "shop",
        [
            "brand",
            polign.Facet("price", ranges=[(None, 500), (500, None)]),
            polign.Facet("category", name="categories", own_filter=True),
            polign.Facet("brand", name="stocked", filter={"in_stock": True}),
        ],
        filter={"category": "tv"},
    )
    req = stub.requests["facets"]
    assert req.collection == "shop" and req.filter_expr.cond.key == "category"
    assert [f.field for f in req.facets] == ["brand", "price", "category", "brand"]
    assert not req.facets[0].own_filter
    low, high = req.facets[1].ranges
    assert not low.HasField("from") and low.to == 500
    assert getattr(high, "from") == 500 and not high.HasField("to")
    assert req.facets[2].own_filter and not req.facets[2].HasField("filter_expr")
    assert req.facets[3].own_filter and req.facets[3].filter_expr.cond.key == "in_stock"
    assert counts.total == 2
    assert counts["brand"].values == [polign.FacetValue("acme", 2)]
    assert counts["brand"].distinct == 1 and counts["price"].distinct is None
    assert counts["price"].ranges == [
        polign.FacetRangeCount(None, 500, 1),
        polign.FacetRangeCount(500, None, 0),
    ]

    ids = client.patch_metadata(
        "shop",
        [polign.MetadataPatch("p1", set={"price": 19.99}), {"id": "p2", "unset": ["promo"]}],
    )
    req = stub.requests["update"]
    assert ids == ["p1", "p2"] and not req.ids
    assert req.updates[0].id == "p1" and req.updates[0].set["price"].number == 19.99
    assert list(req.updates[1].unset) == ["promo"]

    hits = client.search("docs", text="telvison", k=3, fuzzy=True)
    assert stub.requests["search"].fuzzy
    assert [h.id for h in hits] == ["tv"]
    assert hits.corrections == {"telvison": "television"}
