"""A stand-in for ``polign_recall.Client`` (typed tools) used by the unit tests.

Keeps current beliefs in memory with Recall's supersession rule: a new value
for a single-valued predicate replaces the old one.
"""

import re
import threading

from polign_recall import Belief, Earlier, Memory, RecallError, Remembered, RememberResult, TextResult

REGISTRY = [
    {"predicate": "name", "cardinality": "single", "value_type": "string", "description": "Name the subject uses"},
    {"predicate": "timezone", "cardinality": "single", "value_type": "string", "description": "Subject's timezone"},
    {"predicate": "team_size", "cardinality": "single", "value_type": "number", "description": "People on the team"},
    {"predicate": "on_call", "cardinality": "single", "value_type": "boolean", "description": "Is on call"},
    {"predicate": "uses_technology", "cardinality": "multi", "value_type": "string", "description": "Technology used"},
]


class FakeClient:
    def __init__(self) -> None:
        self.beliefs: dict[str, list[Belief]] = {}
        self.calls: list[tuple] = []
        self.fail_reads = False
        self.fail_writes = False
        self.lock = threading.Lock()
        self._cardinality = {p["predicate"]: p["cardinality"] for p in REGISTRY}

    def predicates(self):
        return list(REGISTRY)

    def recall(self, subject=None, predicate=None, *, query=None, limit=None, **_):
        self.calls.append(("recall", subject, query, limit))
        if self.fail_reads:
            raise RecallError("recall is down", code="transport_error")
        found = list(self.beliefs.get(subject, []))
        if query:
            words = set(re.findall(r"\w+", query.lower()))
            found = [b for b in found if words & set(f"{b.predicate} {b.value}".lower().replace("_", " ").split())]
        return found[:limit] if limit else found

    def remember(self, subject, predicate, value, **_):
        self.calls.append(("remember", subject, predicate, value))
        if self.fail_writes:
            raise RecallError("recall is down", code="transport_error")
        with self.lock:
            current = self.beliefs.setdefault(subject, [])
            stored = Belief(subject, predicate, value, 1.0, "agent", "stated", "2026-10-13T00:00:00Z", f"e{len(self.calls)}")
            if any(b.predicate == predicate and b.value == value for b in current):
                return RememberResult(stored, True, ())
            superseded = ()
            if self._cardinality[predicate] == "single":
                superseded = tuple(b for b in current if b.predicate == predicate)
                current[:] = [b for b in current if b.predicate != predicate]
            current.append(stored)
            return RememberResult(stored, False, superseded)

    def forget(self, subject, predicate, value=None, *, all=False):
        self.calls.append(("forget", subject, predicate, value, all))
        current = self.beliefs.get(subject, [])
        gone = [b for b in current if b.predicate == predicate and (all or b.value == value)]
        current[:] = [b for b in current if b not in gone]
        return len(gone)


class FakeTextClient:
    """A stand-in for a ``polign_recall.Client`` serving the text tools.

    Stands in for the server's extraction model with one rule: "X is Y"
    replaces an earlier "X is Z".
    """

    _text = True

    def __init__(self) -> None:
        self.memories: list[Memory] = []
        self.calls: list[tuple] = []
        self.fail_reads = False
        self.fail_writes = False
        self.kept_only = False  # a server with no extraction model
        self.lock = threading.Lock()

    def ask(self, question=None, **_):
        self.calls.append(("ask", question))
        if self.fail_reads:
            raise RecallError("recall is down", code="transport_error")
        found = list(self.memories)
        if question:
            words = set(re.findall(r"\w+", question.lower()))
            found = [m for m in found if words & set(re.findall(r"\w+", m.text.lower()))]
        return found

    def remember(self, *, text, **_):
        self.calls.append(("remember", text))
        if self.fail_writes:
            raise RecallError("recall is down", code="transport_error")
        if self.kept_only:
            return TextResult((), True)
        with self.lock:
            if any(m.text == text for m in self.memories):
                return TextResult((Remembered(text, (), True),), True)
            key = text.split(" is ")[0] + " is " if " is " in text else None
            replaced = tuple(m.text for m in self.memories if key and m.text.startswith(key))
            self.memories = [m for m in self.memories if m.text not in replaced]
            self.memories.append(Memory(text, "2026-10-21", 0, tuple(Earlier(r, "2026-10-01") for r in replaced)))
            return TextResult((Remembered(text, replaced),), True)

    def forget_text(self, text):
        self.calls.append(("forget", text))
        words = set(re.findall(r"\w+", text.lower())) - {"my", "the", "i", "a"}
        gone = [m.text for m in self.memories if words & set(re.findall(r"\w+", m.text.lower()))]
        self.memories = [m for m in self.memories if m.text not in gone]
        return gone
