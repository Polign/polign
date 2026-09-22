import threading

import pytest
import pytest_asyncio
from polign import NotFoundError, Vector
from polign_recall import Belief, RecallError, RememberResult

from recall_vapi import MemoryState, PolignState, RecallMemory, RecallVapi


class FakePolignClient:
    """Enough of polign.Client for PolignState: upsert and point lookup, no CAS."""

    def __init__(self):
        self.records = {}
        self.lock = threading.Lock()

    def put(self, collection, id, values, metadata=None):
        assert collection == "vapi_calls" and list(values) == [1.0]
        with self.lock:
            self.records[id] = dict(metadata or {})
        return id

    def get(self, collection, id, *, typed_metadata=False):
        assert collection == "vapi_calls" and typed_metadata
        with self.lock:
            if id not in self.records:
                raise NotFoundError("no such vector")
            return Vector(id, [1.0], dict(self.records[id]))


class FakeClient:
    """Controllable storage boundary; adapter and call state are real."""

    def __init__(self):
        self.facts = {}
        self.writes = []
        self.read_subjects = []
        self.fail_reads = False
        self.fail_writes = False
        self.started = threading.Event()
        self.release = threading.Event()
        self.release.set()
        self.closed = False

    def predicates(self):
        return [
            {"predicate": "name", "value_type": "string", "cardinality": "single"},
            {"predicate": "consent", "value_type": "boolean", "cardinality": "single"},
            {"predicate": "score", "value_type": "number", "cardinality": "single"},
        ]

    def recall(self, *, subject, query=None, limit=20):
        self.read_subjects.append(subject)
        if self.fail_reads:
            raise RecallError("sensitive backend detail")
        return [
            b
            for (s, _), b in self.facts.items()
            if s == subject and (query is None or query in str(b.value))
        ][:limit]

    def remember(self, subject, predicate, value, *, source):
        self.started.set()
        assert self.release.wait(5), "test forgot to release the blocked write"
        if self.fail_writes:
            raise RecallError("secret credential must not reach Vapi")
        old = self.facts.get((subject, predicate))
        belief = Belief(subject, predicate, value, 1.0, source, "assertion", "2026-09-21", "event")
        self.facts[(subject, predicate)] = belief
        self.writes.append(belief)
        return RememberResult(belief, bool(old and old.value == value), (old,) if old else ())

    def forget(self, subject, predicate, value):
        old = self.facts.get((subject, predicate))
        if old and old.value == value:
            del self.facts[(subject, predicate)]
            return 1
        return 0

    def close(self):
        self.closed = True


@pytest.fixture
def client():
    return FakeClient()


@pytest.fixture
def polign():
    return FakePolignClient()


@pytest.fixture(params=["polign", "memory"])
def state(request, polign):
    if request.param == "polign":
        return PolignState(client=polign)
    return MemoryState()


@pytest_asyncio.fixture
async def adapter(client, state):
    async def resolve(message):
        return {"alice-number": "tenant:alice", "bob-number": "tenant:bob"}.get(
            message["call"].get("customer", {}).get("number")
        )

    bridge = RecallVapi(
        memory=RecallMemory(client),
        state=state,
        assistant={
            "model": {
                "provider": "openai",
                "model": "test",
                "messages": [{"role": "system", "content": "Help the caller."}],
            },
            "serverMessages": ["status-update"],
        },
        tool_server={"url": "https://example.test/vapi/webhook"},
        resolve_subject=resolve,
    )
    yield bridge
    client.release.set()
    await bridge.aclose()


def event(kind, call_id="call-1", **fields):
    return {"message": {"type": kind, "call": {"id": call_id}, **fields}}


def write(tool_id="tool-1", value="Alex", *, name="remember", predicate="name", **args):
    return {
        "id": tool_id,
        "function": {"name": name, "arguments": {"predicate": predicate, "value": value, **args}},
    }


async def run_tool(adapter, tool, call_id="call-1"):
    response = await adapter.handle(event("tool-calls", call_id, toolCallList=[tool]))
    return response["results"][0]


async def start(adapter, call_id="call-1", number="alice-number"):
    payload = event("assistant-request", call_id)
    payload["message"]["call"]["customer"] = {"number": number}
    return (await adapter.handle(payload))["assistant"]
