"""A stand-in for ``polign_recall.Client`` (typed tools) used by the unit tests.

Keeps current beliefs in memory with Recall's supersession rule: a new value
for a single-valued predicate replaces the old one.
"""

import re
import threading

from polign_recall import Belief, RecallError, RememberResult

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
