"""Integration tests against a real polign_db server.

Boots a polign-server binary on free ports, then runs the same scenario
through the HTTP client and (if grpcio is installed) the gRPC client.

The server binary is located, in order, from:

1. ``POLIGN_SERVER``: path to a ``polign-server`` binary.
2. ``POLIGN_SOURCE``: path to a polign_db source checkout; the server is
   built from ``./cmd/server`` with the Go toolchain (used by the private
   source repository's CI to test unreleased server changes).
3. ``POLIGN_SERVER_VERSION`` or, unset, the latest stable release: the
   matching archive is downloaded from the GitHub releases page into
   ``~/.cache/polign-python-tests`` and reused on later runs. Set
   ``POLIGN_SERVER_VERSION=none`` to disable downloading.
4. ``polign-server`` on ``PATH``.

Tests are skipped when no binary can be found.

Run from python/:  pytest tests/test_integration.py -v
"""

import io
import json
import os
import pathlib
import platform
import re
import shutil
import socket
import subprocess
import tarfile
import time
import urllib.request

import pytest

import polign
from polign import Client

RELEASES = "https://github.com/Polign/polign/releases"
CACHE = pathlib.Path(
    os.environ.get("POLIGN_TEST_CACHE", pathlib.Path.home() / ".cache" / "polign-python-tests")
)


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _build_from_source(source, out_dir):
    root = pathlib.Path(source).resolve()
    if shutil.which("go") is None or not (root / "cmd" / "server").is_dir():
        pytest.skip(f"POLIGN_SOURCE={source} needs a Go toolchain and cmd/server")
    binary = out_dir / "polign-server"
    subprocess.run(
        ["go", "build", "-o", str(binary), "./cmd/server"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    return binary


def _release_tag(version):
    if version:
        return version if version.startswith("v") else "v" + version
    with urllib.request.urlopen(
        "https://api.github.com/repos/Polign/polign/releases/latest", timeout=20
    ) as resp:
        return json.load(resp)["tag_name"]


def _download_release(version):
    system = platform.system().lower()
    machine = platform.machine().lower()
    arch = {"x86_64": "amd64", "amd64": "amd64", "arm64": "arm64", "aarch64": "arm64"}.get(machine)
    if system not in ("linux", "darwin") or arch is None:
        pytest.skip(f"no polign-server release for {system}/{machine}")
    try:
        tag = _release_tag(version)
    except Exception as exc:  # offline, rate limited, ...
        pytest.skip(f"could not resolve a polign-server release: {exc}")
    target = CACHE / tag / "polign-server"
    if target.is_file():
        return target
    url = f"{RELEASES}/download/{tag}/polign_db_{system}_{arch}.tar.gz"
    try:
        with urllib.request.urlopen(url, timeout=120) as resp:
            archive = resp.read()
    except Exception as exc:
        pytest.skip(f"could not download {url}: {exc}")
    target.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tar:
        member = tar.getmember("polign-server")
        with tar.extractfile(member) as src, open(target, "wb") as dst:
            shutil.copyfileobj(src, dst)
    target.chmod(0o755)
    return target


def _locate_server(tmp_dir):
    explicit = os.environ.get("POLIGN_SERVER")
    if explicit:
        if not pathlib.Path(explicit).is_file():
            pytest.skip(f"POLIGN_SERVER={explicit} does not exist")
        return pathlib.Path(explicit)
    source = os.environ.get("POLIGN_SOURCE")
    if source:
        return _build_from_source(source, tmp_dir)
    version = os.environ.get("POLIGN_SERVER_VERSION", "")
    if version.lower() != "none":
        return _download_release(version)
    on_path = shutil.which("polign-server")
    if on_path:
        return pathlib.Path(on_path)
    pytest.skip("no polign-server binary: set POLIGN_SERVER, POLIGN_SOURCE, or allow a release download")


@pytest.fixture(scope="module")
def server(tmp_path_factory, request):
    binary = _locate_server(tmp_path_factory.mktemp("bin"))
    version = subprocess.check_output([str(binary), "-version"], text=True, timeout=10).strip()
    http_port, grpc_port = _free_port(), _free_port()
    args = [str(binary), "-http", f"127.0.0.1:{http_port}", "-grpc", f"127.0.0.1:{grpc_port}"]
    if getattr(request, "param", None) == "store":
        args += ["-store", "fs:" + str(tmp_path_factory.mktemp("store")),
                 "-hot-max", "0", "-maintain", "0", "-telemetry=false"]
    proc = subprocess.Popen(
        args,
        cwd=tmp_path_factory.mktemp("data"),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    probe = Client(f"http://127.0.0.1:{http_port}", timeout=2)
    try:
        deadline = time.time() + 15
        while not probe.health():
            if proc.poll() is not None:
                raise RuntimeError(
                    "server exited: " + proc.stderr.read().decode(errors="replace")
                )
            if time.time() > deadline:
                raise RuntimeError("server did not become healthy within 15s")
            time.sleep(0.1)
        yield {"http": http_port, "grpc": grpc_port, "version": version}
    finally:
        probe.close()
        proc.terminate()
        proc.wait(timeout=10)


@pytest.fixture(params=["http", "grpc"])
def client(request, server):
    if request.param == "grpc":
        pytest.importorskip("grpc")
        from polign import GrpcClient

        c = GrpcClient(f"127.0.0.1:{server['grpc']}", timeout=10)
    else:
        c = Client(f"http://127.0.0.1:{server['http']}", timeout=10)
    yield c
    c.close()


def _assert_default_collection_info(info):
    assert info.status == "active"
    assert info.backend.uri == ""
    assert info.backend_id == ""
    assert info.created_at == ""
    assert info.verified_at == ""
    assert info.verified_capabilities == []


def _require_default_listing(server):
    # Public CI also runs against the latest published binary. Skip only
    # releases known to predate this server feature; dev builds must pass.
    release = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", server["version"])
    if release and tuple(map(int, release.groups())) <= (0, 7, 0):
        pytest.skip("server 0.7.0 and earlier require -byo-store for listing")


def test_list_collections_in_memory(client, server, request):
    _require_default_listing(server)
    coll = f"listing-memory-{request.node.callspec.id}"
    client.put(coll, "a", [1.0, 0.0])
    listed = client.list_collections()
    names = [c.name for c in listed]
    assert names == sorted(set(names))
    _assert_default_collection_info(next(c for c in listed if c.name == coll))


@pytest.mark.parametrize("server", ["store"], indirect=True)
def test_list_collections_default_store(client, server, request):
    _require_default_listing(server)
    coll = f"listing-store-{request.node.callspec.id}"
    assert coll not in [c.name for c in client.list_collections()]
    # Reach the training threshold instead of waiting for the server's 30s
    # idle-training delay for a one-vector collection.
    client.put_many(
        coll,
        [polign.Vector(id=f"v{i}", values=[float(i), float(i % 7)]) for i in range(1000)],
    )
    # The embedded persistor publishes asynchronously. Listing must discover
    # its manifest; an acknowledged WAL write alone is not the listing contract.
    deadline = time.monotonic() + 30
    while True:
        listed = client.list_collections()
        names = [c.name for c in listed]
        assert names == sorted(set(names))
        if coll in names:
            _assert_default_collection_info(next(c for c in listed if c.name == coll))
            break
        assert time.monotonic() < deadline, "persisted collection never became discoverable"
        time.sleep(0.1)


def test_round_trip(client, request):
    coll = f"it-{request.node.callspec.id}"

    assert client.put(coll, "a", [1.0, 0.0, 0.0], metadata={"label": "first"}) == "a"
    assert client.put(coll, "b", [0.0, 1.0, 0.0], metadata={"label": "second"}) == "b"
    assert client.put(coll, "c", [0.9, 0.1, 0.0]) == "c"

    v = client.get(coll, "a")
    assert v.id == "a" and v.metadata == {"label": "first"}
    assert [round(x, 4) for x in v.values] == [1.0, 0.0, 0.0]

    page = client.list(coll, limit=2)
    assert page.total == 3 and len(page) == 2
    assert [x.id for x in page] == ["a", "b"]  # ordered by id

    hits = client.search(coll, values=[1.0, 0.0, 0.0], k=2)
    assert [h.id for h in hits] == ["a", "c"]
    assert hits[0].distance <= hits[1].distance
    assert hits[0].score == 0.0  # pure vector search carries no BM25 score

    # upsert replaces in place
    client.put(coll, "a", [0.0, 0.0, 1.0], metadata={"label": "moved"})
    assert client.get(coll, "a").metadata == {"label": "moved"}
    assert client.list(coll).total == 3

    # metadata equality filter
    hits = client.search(coll, values=[1.0, 0.0, 0.0], k=3, filter={"label": "second"})
    assert [h.id for h in hits] == ["b"]

    # operator filters — the same dict language on both transports
    hits = client.search(
        coll, values=[1.0, 0.0, 0.0], k=3, filter={"label": {"$in": ["second", "moved"]}}
    )
    assert sorted(h.id for h in hits) == ["a", "b"]
    hits = client.search(
        coll, values=[1.0, 0.0, 0.0], k=3, filter={"label": {"$exists": False}}
    )
    assert [h.id for h in hits] == ["c"]
    with pytest.raises(polign.InvalidArgumentError):
        client.search(coll, values=[1.0, 0.0, 0.0], k=3, filter={"$bogus": "x"})

    # filtered listing — same dict language; offset/total count matches only
    page = client.list(coll, filter={"label": {"$exists": True}})
    assert page.total == 2 and [x.id for x in page] == ["a", "b"]
    page = client.list(coll, offset=1, filter={"label": {"$exists": True}})
    assert page.total == 2 and [x.id for x in page] == ["b"]
    with pytest.raises(polign.InvalidArgumentError):
        client.list(coll, filter={"$bogus": "x"})

    assert client.delete(coll, "b") is True
    assert client.delete(coll, "b") is False  # idempotent, not an error
    with pytest.raises(polign.NotFoundError):
        client.get(coll, "b")


def test_put_many(client, request):
    from polign import Vector

    coll = f"batch-{request.node.callspec.id}"
    ids = client.put_many(
        coll,
        [
            Vector(id="a", values=[1.0, 0.0], metadata={"label": "first"}),
            Vector(id="b", values=[0.0, 1.0]),
            Vector(id="c", values=[0.9, 0.1]),
        ],
    )
    assert ids == ["a", "b", "c"]
    assert client.list(coll).total == 3
    assert [h.id for h in client.search(coll, values=[1.0, 0.0], k=2)] == ["a", "c"]

    # Whole-batch validation: nothing from an invalid batch is applied.
    with pytest.raises(polign.PolignError) as ei:
        client.put_many(coll, [Vector(id="d", values=[0.5, 0.5]), Vector(id="e", values=[1.0])])
    assert isinstance(ei.value, polign.InvalidArgumentError)
    assert client.list(coll).total == 3

    with pytest.raises(polign.InvalidArgumentError):
        client.put_many(coll, [])

    # Batches are capped at 5000 vectors; an oversized batch applies nothing.
    with pytest.raises(polign.InvalidArgumentError, match="5000"):
        client.put_many(
            coll, [Vector(id=f"x{i}", values=[0.0, 1.0]) for i in range(5001)]
        )
    assert client.list(coll).total == 3


def test_get_many(client, request):
    from polign import Vector

    coll = f"getmany-{request.node.callspec.id}"
    client.put_many(
        coll,
        [
            Vector(id="a", values=[1.0, 0.0], metadata={"label": "first"}),
            Vector(id="b", values=[0.0, 1.0]),
            # float32-representable values, so byte-exactness is comparable
            # across both transports (JSON round-trips decimals, gRPC f32).
            Vector(id="c", values=[0.75, 0.125]),
        ],
    )
    # Request-order results, unknown ids silently omitted.
    vs = client.get_many(coll, ["c", "missing", "a"])
    assert [v.id for v in vs] == ["c", "a"]
    assert vs[0].values == [0.75, 0.125]
    assert vs[1].metadata == {"label": "first"}

    with pytest.raises(polign.InvalidArgumentError):
        client.get_many(coll, [])
    with pytest.raises(polign.NotFoundError):
        client.get_many("no-such-collection", ["a"])


def test_delete_many_and_describe(client, request):
    from polign import Vector

    coll = f"delete-many-{request.node.callspec.id}"
    client.put_many(
        coll,
        [
            Vector(id="a", values=[1.0, 0.0], metadata={"ref_doc_id": "doc-1"}),
            Vector(id="b", values=[0.0, 1.0], metadata={"ref_doc_id": "doc-1"}),
            Vector(id="c", values=[1.0, 1.0], metadata={"ref_doc_id": "doc-2"}),
        ],
    )

    desc = client.describe_collection(coll)
    assert (desc.name, desc.dimension, desc.metric, desc.index_type) == (
        coll,
        2,
        "l2",
        "ivf",
    )
    assert desc.segment_backed is False

    removed = client.delete_many(coll, filter={"ref_doc_id": "doc-1"})
    assert removed.ids == ["a", "b"] and removed.truncated is False
    assert client.delete_many(coll, ids=["c", "missing"]).ids == ["c"]
    assert client.list(coll).total == 0


def test_errors(client, request):
    coll = f"err-{request.node.callspec.id}"
    with pytest.raises(polign.NotFoundError):
        client.get("no-such-collection", "x")
    client.put(coll, "a", [1.0, 0.0])
    with pytest.raises(polign.InvalidArgumentError):
        client.put(coll, "bad-dim", [1.0, 0.0, 0.0])  # dim mismatch
    with pytest.raises(polign.InvalidArgumentError):
        client.search(coll, values=[1.0, 0.0], k=0)  # k must be > 0


def test_typed_metadata(client, request):
    coll = f"typed-{request.node.callspec.id}"

    assert (
        client.put(
            coll,
            "t",
            [1.0, 0.0, 0.0],
            metadata={"topic": "SIP", "score": 0.5, "n": 7, "live": True},
        )
        == "t"
    )

    # Default reads render every value as a string, matching pre-typed
    # clients on both transports.
    v = client.get(coll, "t")
    assert v.metadata == {"topic": "SIP", "score": "0.5", "n": "7", "live": "true"}

    # The opt-in returns stored types (ints come back as floats).
    tv = client.get(coll, "t", typed_metadata=True)
    assert tv.metadata["topic"] == "SIP"
    assert tv.metadata["score"] == 0.5
    assert tv.metadata["n"] == 7.0
    assert tv.metadata["live"] is True

    hits = client.search(coll, [1.0, 0.0, 0.0], 1, typed_metadata=True)
    assert hits and hits[0].metadata["score"] == 0.5

    # Typed filters compare numerically; a string operand does not match a
    # number record.
    assert [
        h.id for h in client.search(coll, [1.0, 0.0, 0.0], 5, filter={"score": {"$gt": 0.4}})
    ] == ["t"]
    assert [h.id for h in client.search(coll, [1.0, 0.0, 0.0], 5, filter={"score": 0.5})] == ["t"]
    assert [h.id for h in client.search(coll, [1.0, 0.0, 0.0], 5, filter={"live": True})] == ["t"]
    assert client.search(coll, [1.0, 0.0, 0.0], 5, filter={"score": "0.5"}) == []


def test_http_grpc_strict_filter_parity(server):
    """Both transports must preserve operand kinds for search and delete."""
    pytest.importorskip("grpc")
    from polign import GrpcClient

    http = Client(f"http://127.0.0.1:{server['http']}", timeout=10)
    grpc = GrpcClient(f"127.0.0.1:{server['grpc']}", timeout=10)
    coll = "strict-filter-parity"
    try:
        http.put(
            coll,
            "string-record",
            [1.0, 0.0],
            metadata={"numberish": "1", "boolish": "true"},
        )

        cross_kind_filters = [
            {"numberish": 1.0},
            {"numberish": {"$gte": 0.0}},
            {"boolish": True},
        ]
        for filter_expr in cross_kind_filters:
            for client in (http, grpc):
                assert client.search(
                    coll, [1.0, 0.0], 5, filter=filter_expr
                ) == []
                removed = client.delete_many(coll, filter=filter_expr)
                assert removed.ids == [] and removed.truncated is False

        for client in (http, grpc):
            assert [
                hit.id
                for hit in client.search(
                    coll, [1.0, 0.0], 5, filter={"numberish": "1"}
                )
            ] == ["string-record"]
            assert client.list(coll).total == 1
    finally:
        http.close()
        grpc.close()
