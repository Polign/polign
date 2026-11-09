"""Drives the Recall memory provider inside an Agent Substrate actor.

The provider is the plugin as shipped, on Hermes's own MemoryProvider base
class and secret scope. This driver stands in for the agent loop: it calls the
hooks Hermes calls around a turn, so the example needs no model and no model
key. POST /turn is a completed turn, POST /prefetch is the start of the next
one, POST /tool is a tool call.
"""

from __future__ import annotations

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import recall_plugin as plugin

SESSION = "substrate-demo"
_lock = threading.Lock()
_provider = None
# Lives in process memory only, to show what a snapshot keeps.
_turns = 0


def provider():
    """The provider, opened on the first request, so nothing dials Recall while
    Substrate takes the template's golden snapshot."""
    global _provider
    with _lock:
        if _provider is None:
            p = plugin.RecallMemoryProvider()
            p.initialize(SESSION, hermes_home=os.environ["HERMES_HOME"], platform="cli", agent_context="primary")
            _provider = p
        return _provider


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # quiet
        pass

    def _send(self, status: int, body) -> None:
        raw = (body if isinstance(body, str) else json.dumps(body)).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        if self.path == "/readyz":
            self._send(200, {"ok": True})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        global _turns
        length = int(self.headers.get("Content-Length") or 0)
        try:
            args = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            self._send(400, {"error": "body must be JSON"})
            return
        p = provider()
        if self.path == "/turn":
            p.sync_turn(args["user"], args["assistant"], session_id=SESSION)
            saved = p.idle(60)
            with _lock:
                _turns += 1
                n = _turns
            self._send(200, {"saved": saved, "turns_in_this_process": n})
        elif self.path == "/prefetch":
            with _lock:
                n = _turns + 1
            p.on_turn_start(n, args["query"])
            self._send(200, {"context": p.prefetch(args["query"], session_id=SESSION), "turns_in_this_process": n - 1})
        elif self.path == "/tool":
            self._send(200, p.handle_tool_call(args["name"], args.get("args") or {}))
        else:
            self._send(404, {"error": "not found"})


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 80), Handler).serve_forever()
