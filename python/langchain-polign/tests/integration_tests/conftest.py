"""Boots a polign-server for the integration tests.

The binary is located like the SDK's own integration tests do: POLIGN_SERVER
(a binary), POLIGN_SOURCE (a polign_db checkout, built with Go),
POLIGN_SERVER_VERSION or the latest release (downloaded and cached under
~/.cache/polign-python-tests), then ``polign-server`` on PATH. Tests are
skipped when nothing is found.
"""

import io
import json
import os
import pathlib
import platform
import shutil
import socket
import subprocess
import tarfile
import time
import urllib.request

import pytest
from polign import Client

RELEASES = "https://github.com/Polign/polign/releases"
CACHE = pathlib.Path(
    os.environ.get("POLIGN_TEST_CACHE", pathlib.Path.home() / ".cache" / "polign-python-tests")
)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _build_from_source(source: str, out_dir: pathlib.Path) -> pathlib.Path:
    root = pathlib.Path(source).resolve()
    if shutil.which("go") is None or not (root / "cmd" / "server").is_dir():
        pytest.skip(f"POLIGN_SOURCE={source} needs a Go toolchain and cmd/server")
    binary = out_dir / "polign-server"
    subprocess.run(
        ["go", "build", "-o", str(binary), "./cmd/server"],
        cwd=root, check=True, capture_output=True, text=True,
    )
    return binary


def _release_tag(version: str) -> str:
    if version:
        return version if version.startswith("v") else "v" + version
    with urllib.request.urlopen(
        "https://api.github.com/repos/Polign/polign/releases/latest", timeout=20
    ) as resp:
        return json.load(resp)["tag_name"]


def _download_release(version: str) -> pathlib.Path:
    system = platform.system().lower()
    machine = platform.machine().lower()
    arch = {"x86_64": "amd64", "amd64": "amd64", "arm64": "arm64", "aarch64": "arm64"}.get(machine)
    if system not in ("linux", "darwin") or arch is None:
        pytest.skip(f"no polign-server release for {system}/{machine}")
    try:
        tag = _release_tag(version)
    except Exception as exc:
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
        with tar.extractfile(tar.getmember("polign-server")) as src, open(target, "wb") as dst:
            shutil.copyfileobj(src, dst)
    target.chmod(0o755)
    return target


def _locate_server(tmp_dir: pathlib.Path) -> pathlib.Path:
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


@pytest.fixture(scope="session")
def polign_url(tmp_path_factory: pytest.TempPathFactory):
    """URL of a freshly started polign-server, torn down after the session."""
    binary = _locate_server(tmp_path_factory.mktemp("bin"))
    http_port, grpc_port = _free_port(), _free_port()
    proc = subprocess.Popen(
        [str(binary), "-http", f"127.0.0.1:{http_port}", "-grpc", f"127.0.0.1:{grpc_port}"],
        cwd=tmp_path_factory.mktemp("data"),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    url = f"http://127.0.0.1:{http_port}"
    probe = Client(url, timeout=2)
    try:
        deadline = time.time() + 15
        while not probe.health():
            if proc.poll() is not None:
                raise RuntimeError("server exited: " + proc.stderr.read().decode(errors="replace"))
            if time.time() > deadline:
                raise RuntimeError("server did not become healthy within 15s")
            time.sleep(0.1)
        yield url
    finally:
        probe.close()
        proc.terminate()
        proc.wait(timeout=10)
