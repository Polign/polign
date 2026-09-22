import importlib
import json
import stat
from pathlib import Path

import pytest


@pytest.fixture
def example(monkeypatch):
    directory = Path(__file__).resolve().parents[4] / "examples/vapi"
    if not directory.exists():
        pytest.skip("example is not part of the installed sdist")
    monkeypatch.syspath_prepend(str(directory))
    return importlib.import_module("live_test")


def test_initialize_preserves_secrets_and_does_not_print_them(
    example, tmp_path, monkeypatch, capsys
):
    for key in (
        "VAPI_WEBHOOK_TOKEN",
        "CALLER_IDENTITY_KEY",
        "RECALL_TENANT",
        "RECALL_DATA_DIR",
        "VAPI_PHONE_NUMBER_ID",
        "VAPI_LIVE_TEST",
    ):
        monkeypatch.delenv(key, raising=False)
    path = tmp_path / ".env"
    example.initialize(path, "test-phone-id")
    first = example.load_env(path)
    example.initialize(path, None)
    second = example.load_env(path)
    assert first == second
    assert first["VAPI_WEBHOOK_TOKEN"] != first["CALLER_IDENTITY_KEY"]
    assert first["VAPI_PHONE_NUMBER_ID"] == "test-phone-id"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    output = capsys.readouterr().out
    assert first["VAPI_WEBHOOK_TOKEN"] not in output
    assert first["CALLER_IDENTITY_KEY"] not in output


def test_environment_is_literal_and_updates_preserve_comments(example, tmp_path, monkeypatch):
    monkeypatch.delenv("VAPI_PHONE_NUMBER_ID", raising=False)
    path = tmp_path / ".env"
    path.write_text("# keep this comment\nVAPI_PHONE_NUMBER_ID=old\n")
    literal = "$(not-a-command) with spaces"
    example.update_env(path, {"VAPI_PHONE_NUMBER_ID": literal})
    assert example.load_env(path)["VAPI_PHONE_NUMBER_ID"] == literal
    assert "# keep this comment" in path.read_text()


def test_configuration_stays_private(example, tmp_path, capsys):
    settings = {
        "PUBLIC_BASE_URL": "https://test.example",
        "VAPI_PHONE_NUMBER_ID": "test-id",
        "VAPI_WEBHOOK_TOKEN": "secret-never-in-console",
        "CALLER_IDENTITY_KEY": "identity-key",
        "RECALL_TENANT": "test",
        "RECALL_DATA_DIR": str(tmp_path),
    }
    example.configure(settings)
    config = tmp_path / "live-test/phone-server.json"
    assert stat.S_IMODE(config.stat().st_mode) == 0o600
    assert (
        json.loads(config.read_text())["server"]["headers"]["Authorization"]
        == "Bearer secret-never-in-console"
    )
    assert "secret-never-in-console" not in capsys.readouterr().out


async def test_live_report_requires_two_observed_calls_and_excludes_synthetic(
    example, adapter, tmp_path
):
    audit_module = importlib.import_module("live_test_audit")
    audit = tmp_path / "events.jsonl"
    bridge = audit_module.AuditedRecallVapi(
        audit_path=audit,
        memory=adapter.memory,
        state=adapter.state,
        assistant=adapter.assistant,
        tool_server=adapter.tool_server,
        resolve_subject=adapter.resolve_subject,
    )

    async def send(kind, call_id, **fields):
        return await bridge.handle(
            {
                "message": {
                    "type": kind,
                    "call": {
                        "id": call_id,
                        "type": "inboundPhoneCall",
                        "customer": {"number": "alice-number"},
                    },
                    **fields,
                }
            }
        )

    await send("assistant-request", "verify-synthetic")
    assert not audit.exists()
    await send("assistant-request", "first")
    for i, name in enumerate(("Alex", "Sam")):
        await send(
            "tool-calls",
            "first",
            toolCallList=[
                {
                    "id": f"tool-{i}",
                    "name": "remember",
                    "arguments": {"predicate": "name", "value": name},
                }
            ],
        )
    await send(
        "end-of-call-report",
        "first",
        endedReason="customer-ended-call",
        artifact={"transcript": "NEVER STORE THIS"},
    )
    await send("assistant-request", "second")
    events = [json.loads(line) for line in audit.read_text().splitlines()]
    incomplete = example.evaluate(events, "first", "second")
    assert incomplete["result"] == "incomplete-or-failed"
    await send("end-of-call-report", "second")
    events = [json.loads(line) for line in audit.read_text().splitlines()]
    result = example.evaluate(events, "first", "second")
    assert result["result"] == "passed"
    assert all(result["checks"].values())
    assert "NEVER STORE THIS" not in audit.read_text()
    assert "alice-number" not in audit.read_text()
    assert stat.S_IMODE(audit.stat().st_mode) == 0o600
    with pytest.raises(ValueError):
        example.evaluate(events, "verify-synthetic", "second")
    assert example.evaluate(events, "second", "first")["result"] != "passed"
    changed = json.loads(json.dumps(events))
    for entry in changed:
        if entry["call_id"] == "second" and entry["type"] == "assistant-request":
            entry["caller_key"] = "another-person"
    assert not example.evaluate(changed, "first", "second")["checks"]["same_resolved_caller"]


def test_no_observed_calls_never_passes(example, tmp_path, capsys):
    assert example.report({"RECALL_DATA_DIR": str(tmp_path)}, None, None) == 2
    assert "No live call webhooks" in capsys.readouterr().out


@pytest.mark.parametrize(
    "origin",
    [
        "http://test.example",
        "https://u:p@test.example",
        "https://test.example/path",
        "https://test.example?key=secret",
    ],
)
def test_webhook_origin_validation(example, origin):
    with pytest.raises(ValueError):
        example.https_origin(origin)


def test_dns_fallback_preserves_tls_hostname_verification(example, monkeypatch):
    import io
    import socket
    import ssl
    import urllib.error
    import urllib.request

    transport = importlib.import_module("live_test_http")
    host = "temporary-test.trycloudflare.com"
    monkeypatch.setattr(transport, "public_address", lambda name: "104.16.231.132")
    opened = []

    def build(*handlers):
        class Opener:
            def open(self, request, timeout):
                if not opened:
                    opened.append("system-dns")
                    raise urllib.error.URLError(socket.gaierror("test DNS failure"))
                handler = next(h for h in handlers if isinstance(h, urllib.request.HTTPSHandler))

                def check_connection(factory, req):
                    client = factory(host, timeout=timeout)
                    assert client.host == host
                    assert client._context.check_hostname
                    assert client._context.verify_mode == ssl.CERT_REQUIRED
                    return io.BytesIO(b'{"status":"ok"}')

                monkeypatch.setattr(handler, "do_open", check_connection)
                return handler.https_open(request)

        return Opener()

    monkeypatch.setattr(transport.urllib.request, "build_opener", build)
    with transport.open_url("https://" + host + "/healthz", timeout=2) as response:
        assert json.load(response)["status"] == "ok"


def test_certificate_errors_do_not_trigger_dns_fallback(example, monkeypatch):
    import ssl
    import urllib.error

    transport = importlib.import_module("live_test_http")

    class Opener:
        def open(self, request, timeout):
            raise urllib.error.URLError(ssl.SSLCertVerificationError("certificate rejected"))

    monkeypatch.setattr(transport.urllib.request, "build_opener", lambda *args: Opener())
    monkeypatch.setattr(
        transport, "public_address", lambda _: pytest.fail("must not bypass TLS errors")
    )
    with pytest.raises(urllib.error.URLError):
        transport.open_url("https://temporary-test.trycloudflare.com", timeout=2)
