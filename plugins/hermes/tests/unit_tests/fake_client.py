"""A stand-in for polign_recall.Client with the text tools, and the result types the plugin reads."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Earlier:
    text: str
    since: str


@dataclass(frozen=True)
class Memory:
    text: str
    since: str
    days_ago: int
    before: tuple = ()
    elsewhere: tuple = ()
    said: str = ""


@dataclass(frozen=True)
class Remembered:
    text: str
    replaces: tuple = ()
    already_known: bool = False


@dataclass(frozen=True)
class TextResult:
    remembered: tuple
    kept_as_text: bool


@dataclass(frozen=True)
class Step:
    when: str = ""
    change: str = ""
    ended: tuple = ()
    began: tuple = ()
    statement: str = ""


@dataclass(frozen=True)
class Timeline:
    about: str
    now: tuple
    changes: tuple


class RecallError(Exception):
    def __init__(self, message: str, *, code: str = "tool_error"):
        super().__init__(message)
        self.code = code


@dataclass
class FakeClient:
    """Records every call; answers from `memories`, or raises `fail` when set."""
    memories: list = field(default_factory=list)
    calls: list = field(default_factory=list)
    fail: Exception | None = None
    delay: float = 0.0
    closed: bool = False
    gate: threading.Event | None = None

    def _hit(self, name: str, **args: Any) -> None:
        self.calls.append((name, args))
        if self.gate is not None:
            self.gate.wait(5)
        if self.fail is not None:
            raise self.fail

    def ask(self, question=None, *, as_of=None, since=None):
        self._hit("ask", question=question, as_of=as_of, since=since)
        if question is None:
            return list(self.memories)
        words = [w.strip("?.!,") for w in question.lower().split()]
        return [m for m in self.memories if any(len(w) >= 4 and w in m.text.lower() for w in words)]

    def remember(self, subject=None, predicate=None, value=None, *, text=None, messages=None, observed_at=None, **_):
        self._hit("remember", text=text, messages=messages, observed_at=observed_at)
        said = text if text is not None else " ".join(m["content"] for m in messages if m["role"] == "user")
        old = [m for m in self.memories if m.text.split()[0] == said.split()[0]]
        self.memories = [m for m in self.memories if m not in old] + [Memory(said, "2026-11-02", 0, tuple(Earlier(o.text, o.since) for o in old))]
        return TextResult((Remembered(said, tuple(o.text for o in old)),), False)

    def forget_text(self, text):
        self._hit("forget", text=text)
        gone = [m.text for m in self.memories if text.lower() in m.text.lower()]
        self.memories = [m for m in self.memories if m.text not in gone]
        return gone

    def history_text(self, question):
        self._hit("history", question=question)
        return [Timeline(question, tuple(m.text for m in self.memories),
                         (Step("2026-10-01T00:00:00Z", "stated", (), ("Lives in Seattle",)),
                          Step("2026-11-02T00:00:00Z", "replaced", ("Lives in Seattle",), ("Lives in Portland",))))]

    def close(self):
        self.closed = True
