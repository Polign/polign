"""Real Recall subprocess, real server, and durable Vapi state; no provider API keys."""

import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from contextlib import contextmanager
from pathlib import Path

import pytest
from polign_db import find_bin

from recall_vapi import RecallMemory, RecallVapi


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@contextmanager
def server(data):
    binary = os.environ.get("POLIGN_SERVER") or find_bin("polign-server")
    cli = os.environ.get("POLIGN_CLI") or (
        str(Path(binary).with_name("polign"))
        if os.environ.get("POLIGN_SERVER")
        else find_bin("polign")
    )
    port, grpc_port = free_port(), free_port()
    while grpc_port == port:
        grpc_port = free_port()
    url = f"http://127.0.0.1:{port}"
    env = {key: value for key, value in os.environ.items() if not key.startswith("POLIGN_")}
    with open(data.parent / "server.log", "ab") as log:
        proc = subprocess.Popen(
            [
                binary,
                "-store",
                f"fs:{data}",
                "-telemetry=false",
                "-http",
                f"127.0.0.1:{port}",
                "-grpc",
                f"127.0.0.1:{grpc_port}",
            ],
            env=env,
            stdout=log,
            stderr=log,
        )
        try:
            deadline = time.monotonic() + 15
            while True:
                try:
                    with urllib.request.urlopen(url + "/healthz", timeout=0.5):
                        break
                except OSError:
                    if proc.poll() is not None or time.monotonic() > deadline:
                        raise RuntimeError("test server failed to start; inspect " + str(log.name))
                    time.sleep(0.05)
            yield url, cli
        finally:
            proc.terminate()
            try:
                proc.wait(10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(5)


def bridge(tmp_path, url, cli):
    memory = RecallMemory.open(
        command=[cli, "mcp", "-memory-only", "-write"],
        timeout=5,
        env={"POLIGN_URL": url, "POLIGN_API_KEY": "", "POLIGN_COLLECTION": "vapi_test"},
    )

    async def resolve(message):
        return "test:alice"

    return RecallVapi(
        memory=memory,
        assistant={"model": {"provider": "openai", "model": "test"}},
        tool_server={"url": "https://example.test/vapi/webhook"},
        resolve_subject=resolve,
        read_timeout=3,
        write_timeout=3,
        forget_tool=True,
    )


async def tool(adapter, tool_id, value, *, name="remember", predicate="name", call_id="first"):
    result = await adapter.handle(
        {
            "message": {
                "type": "tool-calls",
                "call": {"id": call_id},
                "toolCallList": [
                    {
                        "id": tool_id,
                        "type": "function",
                        "function": {
                            "name": name,
                            "arguments": json.dumps({"predicate": predicate, "value": value}),
                        },
                    }
                ],
            }
        }
    )
    response = result["results"][0]
    assert "result" in response, response
    return response


async def test_corrections_withdrawal_and_retry_survive_server_and_adapter_restart(tmp_path):
    data = tmp_path / "memory"
    with server(data) as (url, cli):
        adapter = bridge(tmp_path, url, cli)
        try:
            adapter.bind_call("first", "test:alice")
            original = await tool(adapter, "name-1", "Alex")
            changed = await tool(adapter, "name-2", "Sam")
            assert json.loads(changed["result"])["superseded"] == ["Alex"]
            await tool(adapter, "consent", "false", predicate="consents_to_recording")
            assert len(adapter.memory.client.history("test:alice", "name")) == 2
            # The ledger is ordinary records on the same server as the memory.
            ledger = adapter.state.client.get(
                "vapi_calls", "tool:first:name-1", typed_metadata=True
            )
            assert json.loads(ledger.metadata["response"]) == original
        finally:
            await adapter.aclose()

    with server(data) as (url, cli):
        adapter = bridge(tmp_path, url, cli)
        try:
            # Delayed old tool delivery must not change Sam back to Alex.
            assert await tool(adapter, "name-1", "Alex") == original
            assert len(adapter.memory.client.history("test:alice", "name")) == 2
            second = await adapter.handle(
                {"message": {"type": "assistant-request", "call": {"id": "second"}}}
            )
            prompt = second["assistant"]["model"]["messages"][-1]["content"]
            assert '"Sam"' in prompt and '"Alex"' not in prompt
            assert '"value": false' in prompt
            adapter.bind_call("bob", "test:bob")
            await tool(adapter, "name-1", "Bob", call_id="bob")
            bob = await adapter.prepare_assistant("test:bob")
            assert '"Sam"' not in bob["model"]["messages"][-1]["content"]
            forgotten = await tool(adapter, "forget-name", "Sam", name="forget", call_id="second")
            assert json.loads(forgotten["result"])["withdrawn"] == 1
            assert not adapter.memory.client.recall("test:alice", "name")
        finally:
            await adapter.aclose()

    with server(data) as (url, cli):
        adapter = bridge(tmp_path, url, cli)
        try:
            assert not adapter.memory.client.recall("test:alice", "name")
            assert adapter.memory.client.recall("test:bob", "name")[0].value == "Bob"
        finally:
            await adapter.aclose()


def test_runnable_example_over_http(tmp_path):
    example = Path(__file__).resolve().parents[4] / "examples/vapi"
    if not example.is_dir():
        pytest.skip("example is not included in the package sdist")
    with server(tmp_path / "memory") as (url, _):
        port = free_port()
        origin = f"http://127.0.0.1:{port}"
        env = {key: value for key, value in os.environ.items() if not key.startswith("POLIGN_")}
        env.update(
            {
                "VAPI_WEBHOOK_TOKEN": "test-webhook-token",
                "CALLER_IDENTITY_KEY": "stable-test-identity-key",
                "RECALL_TENANT": "test-tenant",
                "VAPI_PHONE_NUMBER_ID": "test-phone",
                "PUBLIC_BASE_URL": "https://example.test",
                "VERIFY_BASE_URL": origin,
                "RECALL_DATA_DIR": str(tmp_path / "app-data"),
                "POLIGN_URL": url,
                "POLIGN_COLLECTION": "vapi_example_test",
            }
        )
        with open(tmp_path / "app.log", "wb") as log:
            proc = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "uvicorn",
                    "app:app",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(port),
                ],
                cwd=example,
                env=env,
                stdout=log,
                stderr=log,
            )
            try:
                deadline = time.monotonic() + 15
                while True:
                    try:
                        with urllib.request.urlopen(origin + "/healthz", timeout=0.5):
                            break
                    except OSError:
                        if proc.poll() is not None or time.monotonic() > deadline:
                            raise RuntimeError("example failed to start; inspect " + str(log.name))
                        time.sleep(0.05)
                result = subprocess.run(
                    [sys.executable, "verify_memory.py"],
                    cwd=example,
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=20,
                    check=False,
                )
                assert result.returncode == 0, result.stderr
                assert "Passed:" in result.stdout
            finally:
                proc.terminate()
                try:
                    proc.wait(10)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(5)
