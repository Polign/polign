"""Prepare, serve, and inspect an inbound two-call test. No calls are placed by this CLI."""

import argparse
import json
import os
import re
import secrets
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from contextlib import ExitStack
from pathlib import Path

from live_test_audit import evaluate
from live_test_http import open_url
from verify_memory import main as verify_memory

ROOT = Path(__file__).resolve().parent


def load_env(path):
    settings = {}
    if path.exists():
        for line in path.read_text().splitlines():
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            key, separator, value = line.partition("=")
            if not separator or not re.fullmatch(r"[A-Z][A-Z0-9_]*", key.strip()):
                raise ValueError(
                    "Expected literal KEY=value entries in the environment file"
                )
            words = shlex.split(value, comments=True)
            if len(words) > 1:
                raise ValueError("Quote environment values containing spaces")
            settings[key.strip()] = words[0] if words else ""
    return {**settings, **os.environ}


def private_write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(text)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def update_env(path, updates):
    remaining = dict(updates)
    lines = []
    for line in path.read_text().splitlines() if path.exists() else []:
        key = line.partition("=")[0].strip()
        if key in updates:
            if key in remaining:
                lines.append(key + "=" + shlex.quote(remaining.pop(key)))
        else:
            lines.append(line)
    lines.extend(key + "=" + shlex.quote(value) for key, value in remaining.items())
    private_write(path, "\n".join(lines) + "\n")


def initialize(path, phone_id):
    existing = load_env(path)
    defaults = {
        "VAPI_WEBHOOK_TOKEN": secrets.token_urlsafe(32),
        "CALLER_IDENTITY_KEY": secrets.token_urlsafe(32),
        "RECALL_TENANT": "polign-live-test",
        "RECALL_DATA_DIR": str(ROOT / "data"),
        "VAPI_PHONE_NUMBER_ID": "SET_YOUR_VAPI_PHONE_NUMBER_ID",
        "VAPI_LIVE_TEST": "1",
    }
    updates = {key: value for key, value in defaults.items() if not existing.get(key)}
    if phone_id:
        updates["VAPI_PHONE_NUMBER_ID"] = phone_id
    update_env(path, updates)
    print(f"Test environment ready: {path}. Secret values are not printed.")


def validate(settings):
    for key in (
        "VAPI_WEBHOOK_TOKEN",
        "CALLER_IDENTITY_KEY",
        "RECALL_TENANT",
        "VAPI_PHONE_NUMBER_ID",
    ):
        value = settings.get(key, "")
        if not value or value.startswith(("SET_", "replace-", "your-")):
            raise ValueError(f"Set {key} in the environment file first")


def data_dir(settings):
    path = Path(settings.get("RECALL_DATA_DIR", ROOT / "data"))
    return path if path.is_absolute() else ROOT / path


def https_origin(value):
    url = urllib.parse.urlsplit(value)
    if (
        url.scheme != "https"
        or not url.hostname
        or url.username
        or url.password
        or url.query
        or url.fragment
        or url.path not in ("", "/")
    ):
        raise ValueError(
            "PUBLIC_BASE_URL must be an HTTPS origin without credentials, path, or query"
        )
    return value.rstrip("/")


def stop(proc):
    if proc is not None and proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(5)


def health(origin, processes=(), timeout=20):
    deadline = time.monotonic() + timeout
    last_error = "no healthy response"
    while time.monotonic() < deadline:
        if any(p.poll() is not None for p in processes):
            raise RuntimeError("A test process stopped; inspect data/live-test logs")
        try:
            with open_url(origin + "/healthz", timeout=2) as response:
                if json.load(response).get("status") == "ok":
                    return
        except (OSError, ValueError) as exc:
            last_error = str(exc)
        time.sleep(0.2)
    raise RuntimeError(
        "Service did not become reachable at " + origin + ": " + last_error
    )


def serve(path, port, tunnel):
    settings = load_env(path)
    validate(settings)
    # Refuse to tunnel an unrelated service already occupying the requested port.
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", port))
    directory = data_dir(settings) / "live-test"
    directory.mkdir(parents=True, exist_ok=True)
    endpoint = directory / "endpoint.json"
    app = proxy = None
    with ExitStack() as logs:
        try:
            if tunnel:
                binary = shutil.which("cloudflared")
                if not binary:
                    raise ValueError(
                        "Install cloudflared, or use your existing PUBLIC_BASE_URL without --tunnel"
                    )
                tunnel_log = directory / "tunnel.log"
                # Do not inherit a user's named-tunnel credentials or ingress routes.
                tunnel_config = directory / "cloudflared.yml"
                private_write(tunnel_config, "{}\n")
                stream = logs.enter_context(open(tunnel_log, "w"))
                proxy = subprocess.Popen(
                    [
                        binary,
                        "tunnel",
                        "--config",
                        str(tunnel_config),
                        "--url",
                        f"http://127.0.0.1:{port}",
                        "--no-autoupdate",
                    ],
                    stdout=stream,
                    stderr=subprocess.STDOUT,
                    env={
                        key: value
                        for key, value in os.environ.items()
                        if not key.startswith("TUNNEL_")
                    },
                )
                deadline = time.monotonic() + 45
                origin = None
                while time.monotonic() < deadline and proxy.poll() is None:
                    match = re.search(
                        r"https://[a-z0-9-]+\.trycloudflare\.com",
                        tunnel_log.read_text(),
                    )
                    if match:
                        origin = match.group(0)
                        break
                    time.sleep(0.2)
                if origin is None:
                    raise RuntimeError(f"Tunnel did not start; inspect {tunnel_log}")
                update_env(path, {"PUBLIC_BASE_URL": origin})
            else:
                origin = https_origin(settings.get("PUBLIC_BASE_URL", ""))
            settings.update(
                PUBLIC_BASE_URL=origin,
                VAPI_LIVE_TEST="1",
                RECALL_DATA_DIR=str(data_dir(settings)),
            )
            stream = logs.enter_context(open(directory / "app.log", "a"))
            app = subprocess.Popen(
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
                cwd=ROOT,
                env=settings,
                stdout=stream,
                stderr=subprocess.STDOUT,
            )
            processes = [app] + ([proxy] if proxy else [])
            health(f"http://127.0.0.1:{port}", processes)
            health(origin, processes, timeout=45)
            private_write(
                endpoint,
                json.dumps({"origin": origin, "port": port, "active": True}) + "\n",
            )
            print(f"Ready: {origin}/vapi/webhook", flush=True)
            print(
                "Keep this terminal running. In another terminal run live_test.py check, then configure.",
                flush=True,
            )
            print(
                "After two phone calls, run live_test.py report. Ctrl-C stops the app and tunnel.",
                flush=True,
            )
            while all(p.poll() is None for p in processes):
                time.sleep(0.5)
            raise RuntimeError("A test process stopped; inspect data/live-test logs")
        finally:
            stop(app)
            stop(proxy)
            if endpoint.exists():
                endpoint.unlink()


def check(settings):
    validate(settings)
    origin = https_origin(settings.get("PUBLIC_BASE_URL", ""))
    health(origin)
    try:
        open_url(
            urllib.request.Request(origin + "/vapi/webhook", data=b"{}"), timeout=5
        )
    except urllib.error.HTTPError as exc:
        if exc.code != 401:
            raise RuntimeError(
                "Expected an unauthenticated webhook request to return 401"
            ) from None
    else:
        raise RuntimeError("Webhook did not reject an unauthenticated request")
    try:
        # Keep the verified DNS address from the health check for this sequence.
        verify_memory({**settings, "VERIFY_BASE_URL": origin})
    except (OSError, ValueError, RuntimeError, AssertionError):
        diagnostic = data_dir(settings) / "live-test/preflight-error.log"
        private_write(diagnostic, traceback.format_exc())
        raise RuntimeError(
            f"Public memory check failed; inspect {diagnostic}"
        ) from None
    print(
        "HTTPS and webhook authentication passed. This was a synthetic check, not a phone call."
    )


def configure(settings):
    validate(settings)
    origin = https_origin(settings.get("PUBLIC_BASE_URL", ""))
    configuration = {
        "phoneNumberId": settings["VAPI_PHONE_NUMBER_ID"],
        "server": {
            "url": origin + "/vapi/webhook",
            "headers": {
                "Authorization": "Bearer " + settings["VAPI_WEBHOOK_TOKEN"],
            },
        },
    }
    target = data_dir(settings) / "live-test/phone-server.json"
    private_write(target, json.dumps(configuration, indent=2) + "\n")
    print(f"Phone server settings (contains the webhook credential): {target}")
    print(f"In Vapi, select phone number ID {settings['VAPI_PHONE_NUMBER_ID']}.")
    print(
        f"Set its server URL to {origin}/vapi/webhook and leave the assistant unassigned."
    )
    print(
        "Create/select a Bearer Token credential matching VAPI_WEBHOOK_TOKEN in your .env."
    )
    print(
        "Use the Authorization header with the Bearer prefix. Account settings were not changed."
    )
    print(
        "Call 1: say 'My name is Alex', wait, then 'Actually, call me Sam', wait and hang up."
    )
    print(
        "Call 2: call from the same phone and check the greeting remembers Sam; then hang up."
    )


def report(settings, first, second):
    path = data_dir(settings) / "live-test/events.jsonl"
    if not path.exists():
        print(
            "No live call webhooks recorded yet. Start the live test and call your Vapi number."
        )
        return 2
    events = [
        json.loads(line) for line in path.read_text().splitlines() if line.strip()
    ]
    if not first or not second:
        calls = list(
            dict.fromkeys(
                e["call_id"] for e in events if e["type"] == "assistant-request"
            )
        )
        print("Observed call IDs:")
        for call in calls:
            print("  " + call)
        print(
            "Choose the pair with: live_test.py report --first-call ID --second-call ID"
        )
        return 2
    result = evaluate(events, first, second)
    target = path.with_name("report.json")
    private_write(
        target,
        json.dumps({"first_call": first, "second_call": second, **result}, indent=2)
        + "\n",
    )
    print(json.dumps(result, indent=2))
    print(f"Report saved to {target}")
    return 0 if result["result"] == "passed" else 1


def main():
    # Terminating the helper must also close the app and public tunnel it owns.
    def interrupted(_signal, _frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, interrupted)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser(
        "init", help="Generate local secrets without overwriting existing settings"
    )
    init.add_argument("--phone-number-id")
    server = commands.add_parser(
        "serve", help="Run the app, optionally with a temporary HTTPS tunnel"
    )
    server.add_argument("--port", type=int, default=8000)
    server.add_argument("--tunnel", action="store_true")
    commands.add_parser(
        "check", help="Verify public HTTPS, authentication, and synthetic memory calls"
    )
    commands.add_parser(
        "configure", help="Write private phone-server settings and show dashboard steps"
    )
    reports = commands.add_parser(
        "report", help="Evaluate two live calls using observed webhook evidence"
    )
    reports.add_argument("--first-call")
    reports.add_argument("--second-call")
    args = parser.parse_args()
    try:
        if args.command == "init":
            initialize(args.env_file, args.phone_number_id)
        elif args.command == "serve":
            serve(args.env_file, args.port, args.tunnel)
        else:
            settings = load_env(args.env_file)
            if args.command == "check":
                check(settings)
            elif args.command == "configure":
                configure(settings)
            else:
                return report(settings, args.first_call, args.second_call)
    except KeyboardInterrupt:
        print("Test app and tunnel stopped. Memory data is retained.")
    except (ValueError, RuntimeError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
