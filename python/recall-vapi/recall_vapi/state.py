"""Durable call bindings and at-most-once tool execution on a single host."""

from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


class StateError(ValueError):
    """An unbound/closed call, conflicting identity, or reused tool-call ID."""


class SQLiteState:
    """Share one local file between workers; do not place it on a network filesystem.

    Reservations commit before touching Recall. A process crash can leave a
    pending write whose outcome needs manual reconciliation; it is never replayed.
    Keep this file across restarts. It contains customer IDs and tool results.
    """

    def __init__(self, path: str | os.PathLike[str]):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(fd)
        with self._connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS calls (
                    id TEXT PRIMARY KEY, subject TEXT NOT NULL, closed INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS tools (
                    call_id TEXT NOT NULL, tool_id TEXT NOT NULL,
                    fingerprint TEXT NOT NULL, response TEXT,
                    PRIMARY KEY (call_id, tool_id),
                    FOREIGN KEY (call_id) REFERENCES calls(id)
                );
            """)

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=1)
        try:
            db.execute("PRAGMA foreign_keys=ON")
            with db:
                yield db
        finally:
            db.close()

    def bind(self, call_id: str, subject: str) -> None:
        if not isinstance(call_id, str) or not call_id.strip():
            raise StateError("call_id must be a non-empty string")
        if not isinstance(subject, str) or not subject.strip():
            raise StateError("subject must be a non-empty string")
        with self._connection() as db:
            db.execute("INSERT OR IGNORE INTO calls(id, subject) VALUES (?, ?)", (call_id, subject))
            stored, closed = db.execute(
                "SELECT subject, closed FROM calls WHERE id=?", (call_id,)
            ).fetchone()
            if stored != subject or closed:
                raise StateError("call is closed or already bound to another subject")

    def subject(self, call_id: str) -> str:
        with self._connection() as db:
            row = db.execute("SELECT subject, closed FROM calls WHERE id=?", (call_id,)).fetchone()
        if row is None or row[1]:
            raise StateError("call is not bound to an active customer")
        return row[0]

    def finish_call(self, call_id: str) -> None:
        with self._connection() as db:
            # Retain a tombstone even if an end report arrives before setup.
            db.execute(
                "INSERT INTO calls(id, subject, closed) VALUES (?, '', 1) "
                "ON CONFLICT(id) DO UPDATE SET closed=1",
                (call_id,),
            )

    def reserve(self, call_id: str, tool_id: str, fingerprint: str) -> dict | None:
        """None grants execution. Otherwise return the existing result or pending error."""
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT fingerprint, response FROM tools WHERE call_id=? AND tool_id=?",
                (call_id, tool_id),
            ).fetchone()
            if row:
                if row[0] != fingerprint:
                    raise StateError("tool-call ID was reused with different arguments")
                if row[1] is not None:
                    return json.loads(row[1])
                return {
                    "toolCallId": tool_id,
                    "error": (
                        "This operation is still pending or its outcome is unknown. "
                        "Do not claim it succeeded or repeat it with a new tool-call ID."
                    ),
                }
            call = db.execute("SELECT closed FROM calls WHERE id=?", (call_id,)).fetchone()
            if call is None or call[0]:
                raise StateError("call is not bound to an active customer")
            db.execute(
                "INSERT INTO tools(call_id, tool_id, fingerprint) VALUES (?, ?, ?)",
                (call_id, tool_id, fingerprint),
            )
        return None

    def finish_tool(self, call_id: str, tool_id: str, response: dict) -> None:
        with self._connection() as db:
            db.execute(
                "UPDATE tools SET response=? WHERE call_id=? AND tool_id=?",
                (json.dumps(response, allow_nan=False), call_id, tool_id),
            )
