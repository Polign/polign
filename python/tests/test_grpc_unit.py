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
