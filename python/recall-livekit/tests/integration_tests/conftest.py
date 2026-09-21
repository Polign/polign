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
import tempfile
import tarfile
import time
import urllib.request
from contextlib import contextmanager

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
            ["go", "build", "-tags", "cloud", "-o", str(binary), pkg],
            cwd=root, check=True, capture_output=True, text=True
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


@contextmanager
def running_server(server, store, *, env=None, flags=()):
    """Start an isolated server, optionally against a cloud-storage emulator."""
    http_port, grpc_port = _free_port(), _free_port()
    proc = subprocess.Popen(
        [
            str(server), "-store", store, "-telemetry=false",
            "-http", f"127.0.0.1:{http_port}", "-grpc", f"127.0.0.1:{grpc_port}",
            *flags,
        ],
        env=env,
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
        yield url
    finally:
        proc.terminate()
        proc.wait(timeout=10)


@pytest.fixture(scope="session")
def polign(tmp_path_factory: pytest.TempPathFactory):
    """``(url, cli_path)`` of a freshly started polign-server, torn down after the session."""
    server, cli = _locate(tmp_path_factory.mktemp("bin"))
    data = tmp_path_factory.mktemp("data")
    with running_server(server, f"fs:{data}") as url:
        yield url, cli


@pytest.fixture(params=["s3", "gcs"])
def cloud_store(request):
    """A fresh emulator bucket, sanitized server environment, and key listing."""
    env = {
        k: v for k, v in os.environ.items()
        if not k.startswith(("AWS_", "POLIGN_", "GOOGLE_", "GCS_", "STORAGE_EMULATOR_"))
    }
    bucket, prefix = "recall-livekit-test", "memory"
    if request.param == "s3":
        boto3 = pytest.importorskip("boto3")
        moto = pytest.importorskip("moto.server")
        # Check socket permissions before Moto starts a thread that could hang.
        port = _free_port()
        emulator = moto.ThreadedMotoServer(ip_address="127.0.0.1", port=port, verbose=False)
        emulator.start()
        try:
            endpoint = f"http://127.0.0.1:{port}"
            s3 = boto3.client(
                "s3", endpoint_url=endpoint, region_name="us-east-1",
                aws_access_key_id="testing", aws_secret_access_key="testing",
            )
            s3.create_bucket(Bucket=bucket)
            env.update(
                AWS_ACCESS_KEY_ID="testing", AWS_SECRET_ACCESS_KEY="testing",
                AWS_REGION="us-east-1", AWS_ENDPOINT_URL_S3=endpoint,
                AWS_S3_FORCE_PATH_STYLE="true", AWS_EC2_METADATA_DISABLED="true",
                AWS_CONFIG_FILE=os.devnull, AWS_SHARED_CREDENTIALS_FILE=os.devnull,
            )

            def list_keys():
                return [
                    obj["Key"]
                    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix + "/")
                    for obj in page.get("Contents", [])
                ]

            yield f"s3://{bucket}/{prefix}", env, list_keys
        finally:
            emulator.stop()
        return

    binary = os.environ.get("FAKE_GCS_SERVER") or shutil.which("fake-gcs-server")
    if not binary:
        pytest.skip("install fake-gcs-server or set FAKE_GCS_SERVER to enable GCS coverage")
    endpoint = f"http://127.0.0.1:{_free_port()}"
    with tempfile.TemporaryFile() as log:
        proc = subprocess.Popen(
            [binary, "-scheme", "http", "-host", "127.0.0.1", "-port", endpoint.rsplit(":", 1)[1],
             "-backend", "memory", "-external-url", endpoint, "-log-level", "error"],
            env=env, stdout=log, stderr=log,
        )
        try:
            deadline = time.monotonic() + 15
            while True:
                try:
                    with urllib.request.urlopen(endpoint + "/storage/v1/b", timeout=1):
                        break
                except OSError:
                    if proc.poll() is not None or time.monotonic() > deadline:
                        log.seek(0)
                        raise RuntimeError("GCS emulator failed to start: " + log.read().decode(errors="replace"))
                    time.sleep(0.1)
            req = urllib.request.Request(
                endpoint + "/storage/v1/b?project=testing", data=json.dumps({"name": bucket}).encode(),
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=5):
                pass
            env["STORAGE_EMULATOR_HOST"] = endpoint

            def list_keys():
                with urllib.request.urlopen(endpoint + f"/storage/v1/b/{bucket}/o?prefix={prefix}/", timeout=5) as resp:
                    return [obj["name"] for obj in json.load(resp).get("items", [])]

            yield f"gcs://{bucket}/{prefix}", env, list_keys
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
