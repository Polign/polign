"""Boots a polign-server and finds the ``polign`` CLI for the integration tests.

Binaries are located like the SDK's own integration tests do: POLIGN_SERVER
(the server binary; the CLI is expected next to it or on PATH), POLIGN_SOURCE
(a polign_db checkout, built with Go), POLIGN_SERVER_VERSION or the latest
release (downloaded and cached under ~/.cache/polign-python-tests), then
``polign-server`` on PATH. Tests are skipped when nothing is found.
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

RELEASES = "https://github.com/Polign/polign/releases"
CACHE = pathlib.Path(
    os.environ.get("POLIGN_TEST_CACHE", pathlib.Path.home() / ".cache" / "polign-python-tests")
)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _build_from_source(source: str, out_dir: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path]:
    root = pathlib.Path(source).resolve()
    if shutil.which("go") is None or not (root / "cmd" / "server").is_dir():
        pytest.skip(f"POLIGN_SOURCE={source} needs a Go toolchain and cmd/server")
    server, cli = out_dir / "polign-server", out_dir / "polign"
    for binary, pkg in ((server, "./cmd/server"), (cli, "./cmd/polign")):
        subprocess.run(
            ["go", "build", "-o", str(binary), pkg], cwd=root, check=True, capture_output=True, text=True
        )
    return server, cli


def _release_tag(version: str) -> str:
    if version:
        return version if version.startswith("v") else "v" + version
    with urllib.request.urlopen(
        "https://api.github.com/repos/Polign/polign/releases/latest", timeout=20
    ) as resp:
        return json.load(resp)["tag_name"]


def _download_release(version: str) -> tuple[pathlib.Path, pathlib.Path]:
    system = platform.system().lower()
    machine = platform.machine().lower()
    arch = {"x86_64": "amd64", "amd64": "amd64", "arm64": "arm64", "aarch64": "arm64"}.get(machine)
    if system not in ("linux", "darwin") or arch is None:
        pytest.skip(f"no polign release for {system}/{machine}")
    try:
        tag = _release_tag(version)
    except Exception as exc:
        pytest.skip(f"could not resolve a polign release: {exc}")
    server, cli = CACHE / tag / "polign-server", CACHE / tag / "polign"
    if server.is_file() and cli.is_file():
        return server, cli
    url = f"{RELEASES}/download/{tag}/polign_db_{system}_{arch}.tar.gz"
    try:
        with urllib.request.urlopen(url, timeout=120) as resp:
            archive = resp.read()
    except Exception as exc:
        pytest.skip(f"could not download {url}: {exc}")
    server.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tar:
        for target in (server, cli):
            with tar.extractfile(tar.getmember(target.name)) as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst)
            target.chmod(0o755)
    return server, cli


def _locate(tmp_dir: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path]:
    explicit = os.environ.get("POLIGN_SERVER")
    if explicit:
        server = pathlib.Path(explicit)
        if not server.is_file():
            pytest.skip(f"POLIGN_SERVER={explicit} does not exist")
        cli = server.with_name("polign")
        if not cli.is_file():
            on_path = shutil.which("polign")
            if not on_path:
                pytest.skip("no polign CLI next to POLIGN_SERVER or on PATH")
            cli = pathlib.Path(on_path)
        return server, cli
    source = os.environ.get("POLIGN_SOURCE")
    if source:
        return _build_from_source(source, tmp_dir)
    version = os.environ.get("POLIGN_SERVER_VERSION", "")
    if version.lower() != "none":
        return _download_release(version)
    server, cli = shutil.which("polign-server"), shutil.which("polign")
    if server and cli:
        return pathlib.Path(server), pathlib.Path(cli)
    pytest.skip("no polign binaries: set POLIGN_SERVER, POLIGN_SOURCE, or allow a release download")


@pytest.fixture(scope="session")
def polign(tmp_path_factory: pytest.TempPathFactory):
    """``(url, cli_path)`` of a freshly started polign-server, torn down after the session."""
    server, cli = _locate(tmp_path_factory.mktemp("bin"))
    http_port, grpc_port = _free_port(), _free_port()
    data = tmp_path_factory.mktemp("data")
    proc = subprocess.Popen(
        [
            str(server), "-store", f"fs:{data}", "-telemetry=false",
            "-http", f"127.0.0.1:{http_port}", "-grpc", f"127.0.0.1:{grpc_port}",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    url = f"http://127.0.0.1:{http_port}"
    try:
        deadline = time.time() + 15
        while True:
            try:
                with urllib.request.urlopen(f"{url}/healthz", timeout=1) as resp:
                    if resp.status == 200:
                        break
            except Exception:
                pass
            if proc.poll() is not None:
                raise RuntimeError("server exited: " + proc.stderr.read().decode(errors="replace"))
            if time.time() > deadline:
                raise RuntimeError("server did not become healthy within 15s")
            time.sleep(0.1)
        yield url, cli
    finally:
        proc.terminate()
        proc.wait(timeout=10)
