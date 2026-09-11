"""Unit tests for the HTTP client against a canned stub server."""

import json
import math
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

import polign
from polign import Client, Fusion


class _StubHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    # (status, body_dict, extra_headers) keyed by "METHOD path"; the server
    # also records every request as (method, path, headers, body).
    routes = {}
    requests = []

    def _handle(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        type(self).requests.append(
            (self.command, self.path, dict(self.headers), body)
        )
        status, payload, extra = self.routes.get(
            f"{self.command} {self.path}", (500, {"error": "no stub route"}, {})
        )
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        for k, v in extra.items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    do_GET = do_POST = do_PUT = do_DELETE = _handle

    def log_message(self, *args):
        pass


@pytest.fixture()
def stub():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _StubHandler)
    _StubHandler.routes = {}
    _StubHandler.requests = []
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    client = Client(f"http://127.0.0.1:{server.server_address[1]}", timeout=5)
    yield _StubHandler, client
    client.close()
    server.shutdown()


class _FakeResponse:
    def __init__(self, payload):
        self.status = 200
        self.headers = {}
        self._data = json.dumps(payload).encode()

    def read(self):
        return self._data


class _AmbiguousConnection:
    """Records a sent request, then either drops before the response or wins."""

    def __init__(self, name, requests, *, fail, payload):
        self.name = name
        self.requests = requests
        self.fail = fail
        self.payload = payload

    def request(self, method, path, body=None, headers=None):
        self.requests.append((self.name, method, path, body, headers))

    def getresponse(self):
        if self.fail:
            raise OSError("connection dropped after request was sent")
        return _FakeResponse(self.payload)

    def close(self):
        pass


def _client_with_ambiguous_first_response(payload):
    client = Client("http://fault.test", timeout=1)
    requests = []
    first = _AmbiguousConnection(
        "first", requests, fail=True, payload=payload
    )
    retry = _AmbiguousConnection(
        "retry", requests, fail=False, payload=payload
    )
    client._conn = lambda fresh=False: retry if fresh else first
    return client, requests


@pytest.mark.parametrize(
    "operation",
    [
        pytest.param(lambda c: c.put("docs", "a", [1.0]), id="put"),
        pytest.param(
            lambda c: c.put_many(
                "docs", [polign.Vector(id="a", values=[1.0])]
            ),
            id="put-many",
        ),
        pytest.param(lambda c: c.delete("docs", "a"), id="delete"),
        pytest.param(
            lambda c: c.delete_many("docs", ids=["a"]), id="delete-many-ids"
        ),
        pytest.param(
            lambda c: c.delete_many("docs", filter={"kind": "a"}),
            id="delete-many-filter",
        ),
        pytest.param(
            lambda c: c.create_collection(
                "docs", polign.CollectionBackend(uri="s3://bucket/docs")
            ),
            id="create-collection",
        ),
        pytest.param(
            lambda c: c.delete_collection("docs"), id="delete-collection"
        ),
        pytest.param(
            lambda c: c.verify_collection("docs"), id="verify-collection"
        ),
    ],
)
def test_mutations_are_not_retried_after_ambiguous_failure(operation):
    client, requests = _client_with_ambiguous_first_response({"ok": True})
    try:
        with pytest.raises(polign.ConnectionError, match="outcome is unknown"):
            operation(client)
    finally:
        client.close()
    assert [request[0] for request in requests] == ["first"]


@pytest.mark.parametrize(
    ("operation", "payload"),
    [
        pytest.param(
            lambda c: c.get("docs", "a"),
            {"id": "a", "values": []},
            id="get",
        ),
        pytest.param(
            lambda c: c.get_many("docs", ["a"]),
            {"vectors": []},
            id="get-many-post",
        ),
        pytest.param(
            lambda c: c.list("docs"),
            {"vectors": [], "total": 0},
            id="list",
        ),
        pytest.param(
            lambda c: c.describe_collection("docs"), {}, id="describe"
        ),
        pytest.param(
            lambda c: c.search("docs", [1.0]),
            {"hits": []},
            id="search-post",
        ),
        pytest.param(
            lambda c: c.get_collection("docs"), {}, id="get-collection"
        ),
        pytest.param(
            lambda c: c.list_collections(),
            {"collections": []},
            id="list-collections",
        ),
        pytest.param(
            lambda c: c.backend_setup("s3://bucket/docs"),
            {},
            id="backend-setup",
        ),
        pytest.param(lambda c: c.health(), {}, id="health"),
    ],
)
def test_reads_retry_once_after_ambiguous_failure(operation, payload):
    client, requests = _client_with_ambiguous_first_response(payload)
    try:
        operation(client)
    finally:
        client.close()
    assert [request[0] for request in requests] == ["first", "retry"]
    assert requests[0][1:] == requests[1][1:]


def test_put_sends_put_with_id_in_path(stub):
    handler, client = stub
    handler.routes["PUT /v1/collections/docs/vectors/doc-1"] = (200, {"id": "doc-1"}, {})
    assert client.put("docs", "doc-1", [1, 0.5], metadata={"a": "b"}) == "doc-1"
    method, path, headers, body = handler.requests[0]
    assert json.loads(body) == {"values": [1.0, 0.5], "metadata": {"a": "b"}}
    assert headers["Content-Type"] == "application/json"


def test_put_omits_empty_metadata(stub):
    handler, client = stub
    handler.routes["PUT /v1/collections/docs/vectors/x"] = (200, {"id": "x"}, {})
    client.put("docs", "x", [1.0])
    assert json.loads(handler.requests[0][3]) == {"values": [1.0]}


def test_get_and_not_found(stub):
    handler, client = stub
    handler.routes["GET /v1/collections/docs/vectors/a"] = (
        200,
        {"id": "a", "values": [1.0], "metadata": {"k": "v"}},
        {},
    )
    v = client.get("docs", "a")
    assert (v.id, v.values, v.metadata) == ("a", [1.0], {"k": "v"})
    handler.routes["GET /v1/collections/docs/vectors/nope"] = (
        404,
        {"error": "vector not found"},
        {},
    )
    with pytest.raises(polign.NotFoundError, match="vector not found"):
        client.get("docs", "nope")


def test_get_many_sends_batch_and_parses(stub):
    handler, client = stub
    handler.routes["POST /v1/collections/docs/vectors:get"] = (
        200,
        {"vectors": [{"id": "c", "values": [3.0]}, {"id": "a", "values": [1.0]}]},
        {},
    )
    vs = client.get_many("docs", ["c", "missing", "a"])
    assert json.loads(handler.requests[0][3]) == {"ids": ["c", "missing", "a"]}
    assert [(v.id, v.values) for v in vs] == [("c", [3.0]), ("a", [1.0])]


def test_list_page(stub):
    handler, client = stub
    handler.routes["GET /v1/collections/docs/vectors?limit=2&offset=1"] = (
        200,
        {"vectors": [{"id": "a", "values": [1.0]}], "total": 7},
        {},
    )
    page = client.list("docs", limit=2, offset=1)
    assert page.total == 7 and len(page) == 1 and page.vectors[0].id == "a"


def test_list_filter_encoded_in_query(stub):
    handler, client = stub
    path = (
        "GET /v1/collections/docs/vectors?limit=0&offset=0"
        "&filter=%7B%22color%22%3A%20%22red%22%7D"
    )
    handler.routes[path] = (
        200,
        {"vectors": [{"id": "a", "values": [1.0], "metadata": {"color": "red"}}], "total": 1},
        {},
    )
    page = client.list("docs", filter={"color": "red"})
    assert page.total == 1 and page.vectors[0].metadata == {"color": "red"}
    # no filter → no filter param on the wire
    handler.routes["GET /v1/collections/docs/vectors?limit=0&offset=0"] = (
        200,
        {"vectors": [], "total": 0},
        {},
    )
    client.list("docs")
    assert "filter" not in handler.requests[-1][1]


def test_delete_missing_returns_false(stub):
    handler, client = stub
    handler.routes["DELETE /v1/collections/docs/vectors/gone"] = (
        404,
        {"error": "vector not found"},
        {},
    )
    assert client.delete("docs", "gone") is False


def test_delete_many_and_describe(stub):
    handler, client = stub
    handler.routes["POST /v1/collections/docs/vectors:delete"] = (
        200,
        {"ids": ["a", "b"]},
        {},
    )
    result = client.delete_many("docs", filter={"ref_doc_id": "doc-1"})
    assert result.ids == ["a", "b"]
    assert result.truncated is False
    assert json.loads(handler.requests[-1][3]) == {
        "filter": {"ref_doc_id": "doc-1"}
    }

    handler.routes["GET /v1/collections/docs/describe"] = (
        200,
        {
            "name": "docs",
            "dimension": 1536,
            "metric": "cosine",
            "index_type": "ivfpq",
            "segment_backed": True,
            "text_field": "text",
        },
        {},
    )
    desc = client.describe_collection("docs")
    assert (desc.dimension, desc.metric, desc.index_type) == (1536, "cosine", "ivfpq")
    assert desc.segment_backed and desc.text_field == "text"


@pytest.mark.parametrize(
    ("method", "kwargs"),
    [
        ("list", {"filter": {"score": math.nan}}),
        ("search", {"values": [1.0], "filter": {"score": {"$in": [math.inf]}}}),
        ("delete_many", {"filter": {"score": {"$ne": -math.inf}}}),
    ],
)
def test_filter_non_finite_rejected_before_http_request(stub, method, kwargs):
    handler, client = stub
    with pytest.raises(polign.InvalidArgumentError, match="finite"):
        getattr(client, method)("docs", **kwargs)
    assert handler.requests == []


def test_search_body_minimal_and_full(stub):
    handler, client = stub
    handler.routes["POST /v1/collections/docs/query"] = (
        200,
        {"hits": [{"id": "a", "distance": 0.25, "metadata": {"m": "1"}}]},
        {},
    )
    hits = client.search("docs", values=[1, 0], k=5)
    assert json.loads(handler.requests[0][3]) == {"k": 5, "values": [1.0, 0.0]}
    # score is omitted on pure vector search and must default to 0.0
    assert hits[0].score == 0.0 and hits[0].distance == 0.25

    client.search(
        "docs",
        values=[1.0],
        k=3,
        ef=64,
        cold=True,
        nprobe=8,
        filter={"label": "x"},
        text="fox",
        fusion=Fusion(method="linear", alpha=0.6),
        rescore=-1,
    )
    assert json.loads(handler.requests[1][3]) == {
        "k": 3,
        "values": [1.0],
        "ef": 64,
        "cold": True,
        "nprobe": 8,
        "filter": {"label": "x"},
        "text": "fox",
        "fusion": {"method": "linear", "alpha": 0.6, "rrf_k": 60},
        "rescore": -1,
    }


def test_auth_header(stub):
    handler, base_client = stub
    handler.routes["GET /v1/collections/c/vectors/i"] = (200, {"id": "i", "values": [1.0]}, {})
    # Second client against the same stub server, this time with credentials.
    creds = Client(
        f"http://127.0.0.1:{base_client._port}",
        api_key="plgn_abc_def",
        timeout=5,
    )
    try:
        creds.get("c", "i")
    finally:
        creds.close()
    headers = handler.requests[-1][2]
    assert headers["Authorization"] == "Bearer plgn_abc_def"


def test_error_mapping(stub):
    handler, client = stub
    cases = [
        (400, polign.InvalidArgumentError),
        (401, polign.AuthenticationError),
        (403, polign.PermissionDeniedError),
        (429, polign.RateLimitError),
        (503, polign.UnavailableError),
        (500, polign.ServerError),
    ]
    for status, exc in cases:
        handler.routes["GET /v1/collections/c/vectors/i"] = (status, {"error": "boom"}, {})
        with pytest.raises(exc):
            client.get("c", "i")


def test_not_owner_carries_owner(stub):
    handler, client = stub
    handler.routes["GET /v1/collections/c/vectors/i"] = (
        421,
        {"error": "not owner", "owner": "node-2"},
        {"X-Polign-Owner": "node-2"},
    )
    with pytest.raises(polign.NotOwnerError) as ei:
        client.get("c", "i")
    assert ei.value.owner == "node-2"


def test_path_segments_are_encoded(stub):
    handler, client = stub
    handler.routes["GET /v1/collections/c/vectors/a%2Fb%20c"] = (
        200,
        {"id": "a/b c", "values": [1.0]},
        {},
    )
    assert client.get("c", "a/b c").id == "a/b c"


def test_connection_error_when_server_down():
    client = Client("http://127.0.0.1:1", timeout=1)
    with pytest.raises(polign.ConnectionError):
        client.get("c", "i")
    assert client.health() is False


def test_numpy_like_values_are_coerced(stub):
    handler, client = stub
    handler.routes["PUT /v1/collections/docs/vectors/n"] = (200, {"id": "n"}, {})

    class FakeArray:
        def tolist(self):
            return [1.5, 2.5]

    client.put("docs", "n", FakeArray())
    assert json.loads(handler.requests[0][3])["values"] == [1.5, 2.5]


def test_put_many_wire_shape(stub):
    handler, client = stub
    handler.routes["POST /v1/collections/docs/vectors:batch"] = (
        200,
        {"ids": ["a", "b"]},
        {},
    )
    from polign import Vector

    ids = client.put_many(
        "docs",
        [
            Vector(id="a", values=[1, 0], metadata={"k": "v"}),
            Vector(id="b", values=[0, 1]),
        ],
    )
    assert ids == ["a", "b"]
    method, path, _, body = handler.requests[0]
    assert (method, path) == ("POST", "/v1/collections/docs/vectors:batch")
    assert json.loads(body) == {
        "vectors": [
            {"id": "a", "values": [1.0, 0.0], "metadata": {"k": "v"}},
            {"id": "b", "values": [0.0, 1.0]},
        ]
    }


def test_put_many_invalid_batch(stub):
    handler, client = stub
    handler.routes["POST /v1/collections/docs/vectors:batch"] = (
        400,
        {"error": "vector 1 has dimension 1, batch started with 2"},
        {},
    )
    from polign import Vector

    with pytest.raises(polign.InvalidArgumentError, match="dimension"):
        client.put_many("docs", [Vector(id="a", values=[1, 0]), Vector(id="b", values=[1])])


def test_collection_lifecycle(stub):
    handler, client = stub
    info = {
        "name": "docs",
        "status": "pending",
        "backend": {"uri": "s3://bkt/docs", "role_arn": "arn:aws:iam::1:role/r"},
        "claim_token": "tok",
        "claim_path": ".polign/claim",
        "created_at": "2026-08-13T00:00:00Z",
    }
    handler.routes["POST /v1/collections/docs"] = (201, info, {})
    created = client.create_collection(
        "docs",
        polign.CollectionBackend(uri="s3://bkt/docs", role_arn="arn:aws:iam::1:role/r"),
    )
    assert json.loads(handler.requests[-1][3]) == {
        "backend": {"uri": "s3://bkt/docs", "role_arn": "arn:aws:iam::1:role/r"}
    }
    assert created.status == "pending" and created.claim_token == "tok"

    handler.routes["GET /v1/collections/docs"] = (
        200,
        {"name": "docs", "status": "active", "backend": {"uri": "s3://bkt/docs"}},
        {},
    )
    assert client.get_collection("docs").status == "active"

    handler.routes["GET /v1/collections"] = (
        200,
        {"collections": [{"name": "docs", "status": "active"}, {"name": "img", "status": "pending"}]},
        {},
    )
    assert [c.name for c in client.list_collections()] == ["docs", "img"]

    handler.routes["POST /v1/collections/docs/verify"] = (
        200,
        {"name": "docs", "status": "active"},
        {},
    )
    assert client.verify_collection("docs").status == "active"

    handler.routes["DELETE /v1/collections/docs"] = (200, {"deleted": True}, {})
    assert client.delete_collection("docs") is True


def test_collection_api_not_enabled(stub):
    handler, client = stub
    handler.routes["GET /v1/collections"] = (
        501,
        {"error": "collections API disabled (start the server with -byo-store)"},
        {},
    )
    with pytest.raises(polign.NotEnabledError):
        client.list_collections()
