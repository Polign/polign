"""Call bindings and at-most-once tool execution, stored in Polign next to memory."""

from __future__ import annotations

import json
import threading
from collections import OrderedDict
from typing import Any, Protocol

from polign import Client, NotFoundError

PENDING_ERROR = (
    "This operation is still pending or its outcome is unknown. "
    "Do not claim it succeeded or repeat it with a new tool-call ID."
)


class StateError(ValueError):
    """An unbound/closed call, conflicting identity, or reused tool-call ID."""


class CallState(Protocol):
    """What the adapter needs from call state. Implement this for other storage."""

    def bind(self, call_id: str, subject: str) -> None: ...

    def subject(self, call_id: str) -> str: ...

    def finish_call(self, call_id: str) -> None: ...

    def reserve(self, call_id: str, tool_id: str, fingerprint: str) -> dict | None: ...

    def finish_tool(self, call_id: str, tool_id: str, response: dict) -> None: ...


def _check_binding(call_id: str, subject: str) -> None:
    if not isinstance(call_id, str) or not call_id.strip():
        raise StateError("call_id must be a non-empty string")
    if not isinstance(subject, str) or not subject.strip():
        raise StateError("subject must be a non-empty string")


class PolignState:
    """The default: bindings and the tool ledger live in Polign, next to memory.

    One collection (``vapi_calls`` unless renamed) on the same server Recall
    uses, so every worker and host that shares the memory shares the call
    state, and one backup and retention policy covers both. Records are one
    element vectors with typed metadata; they are kept until you delete them.

    Polign has no insert-if-absent write, so a reservation is a pending record
    written before the operation runs. A retry always finds it; only two
    deliveries of the same tool call arriving within the same few milliseconds
    could both execute.
    """

    def __init__(
        self,
        url: str | None = None,
        *,
        api_key: str | None = None,
        collection: str = "vapi_calls",
        timeout: float = 2.0,
        client: Any = None,
    ):
        if not isinstance(collection, str) or not collection.strip():
            raise ValueError("collection must be a non-empty string")
        if client is None:
            if not url:
                raise ValueError("PolignState needs the server url (or a client)")
            client = Client(url, api_key=api_key, timeout=timeout)
        self.client = client
        self.collection = collection

    @classmethod
    def for_memory(cls, memory: Any, **options: Any) -> PolignState:
        """Use the server that ``RecallMemory.open`` connected to."""
        connection = getattr(memory, "connection", None)
        if not connection:
            raise ValueError(
                "this RecallMemory does not know its server; open it with RecallMemory.open "
                "or pass state= explicitly"
            )
        return cls(connection["url"], api_key=connection.get("api_key"), **options)

    def _get(self, record_id: str) -> dict | None:
        try:
            vector = self.client.get(self.collection, record_id, typed_metadata=True)
        except NotFoundError:
            return None
        return dict(vector.metadata or {})

    def _put(self, record_id: str, metadata: dict) -> None:
        self.client.put(self.collection, record_id, [1.0], metadata=metadata)

    def bind(self, call_id: str, subject: str) -> None:
        _check_binding(call_id, subject)
        call = self._get("call:" + call_id)
        if call is None:
            self._put("call:" + call_id, {"kind": "call", "subject": subject, "closed": False})
            return
        if call.get("subject") != subject or call.get("closed"):
            raise StateError("call is closed or already bound to another subject")

    def subject(self, call_id: str) -> str:
        call = self._get("call:" + call_id)
        if call is None or call.get("closed") or not call.get("subject"):
            raise StateError("call is not bound to an active customer")
        return str(call["subject"])

    def finish_call(self, call_id: str) -> None:
        call = self._get("call:" + call_id)
        # Retain a tombstone even if an end report arrives before setup.
        subject = "" if call is None else str(call.get("subject", ""))
        self._put("call:" + call_id, {"kind": "call", "subject": subject, "closed": True})

    def reserve(self, call_id: str, tool_id: str, fingerprint: str) -> dict | None:
        """None grants execution. Otherwise return the existing result or pending error."""
        record_id = "tool:" + call_id + ":" + tool_id
        tool = self._get(record_id)
        if tool is not None:
            if tool.get("fingerprint") != fingerprint:
                raise StateError("tool-call ID was reused with different arguments")
            response = tool.get("response")
            if isinstance(response, str) and response:
                return json.loads(response)
            return {"toolCallId": tool_id, "error": PENDING_ERROR}
        call = self._get("call:" + call_id)
        if call is None or call.get("closed"):
            raise StateError("call is not bound to an active customer")
        self._put(
            record_id,
            {"kind": "tool", "call_id": call_id, "tool_id": tool_id, "fingerprint": fingerprint},
        )
        return None

    def finish_tool(self, call_id: str, tool_id: str, response: dict) -> None:
        record_id = "tool:" + call_id + ":" + tool_id
        tool = self._get(record_id)
        if tool is None:
            return
        tool["response"] = json.dumps(response, allow_nan=False)
        self._put(record_id, tool)


class MemoryState:
    """Bindings and the tool ledger in this process only. For tests and one-off runs.

    Nothing here survives a restart, and worker processes do not share it, so
    a duplicate webhook delivered to a different worker, or after a restart,
    executes again. Production uses PolignState.
    """

    def __init__(self, max_calls: int = 10_000):
        if max_calls < 1:
            raise ValueError("max_calls must be positive")
        self.max_calls = max_calls
        self._lock = threading.Lock()
        self._calls: OrderedDict[str, tuple[str, bool]] = OrderedDict()
        self._tools: dict[tuple[str, str], tuple[str, dict | None]] = {}

    def _insert_call(self, call_id: str, subject: str, closed: bool) -> None:
        self._calls[call_id] = (subject, closed)
        while len(self._calls) > self.max_calls:
            oldest, _ = self._calls.popitem(last=False)
            for key in [k for k in self._tools if k[0] == oldest]:
                del self._tools[key]

    def bind(self, call_id: str, subject: str) -> None:
        _check_binding(call_id, subject)
        with self._lock:
            if call_id not in self._calls:
                self._insert_call(call_id, subject, False)
            stored, closed = self._calls[call_id]
            if stored != subject or closed:
                raise StateError("call is closed or already bound to another subject")

    def subject(self, call_id: str) -> str:
        with self._lock:
            row = self._calls.get(call_id)
        if row is None or row[1]:
            raise StateError("call is not bound to an active customer")
        return row[0]

    def finish_call(self, call_id: str) -> None:
        with self._lock:
            row = self._calls.get(call_id)
            if row is None:
                # Retain a tombstone even if an end report arrives before setup.
                self._insert_call(call_id, "", True)
            else:
                self._calls[call_id] = (row[0], True)

    def reserve(self, call_id: str, tool_id: str, fingerprint: str) -> dict | None:
        """None grants execution. Otherwise return the existing result or pending error."""
        with self._lock:
            row = self._tools.get((call_id, tool_id))
            if row:
                if row[0] != fingerprint:
                    raise StateError("tool-call ID was reused with different arguments")
                if row[1] is not None:
                    return json.loads(json.dumps(row[1]))
                return {"toolCallId": tool_id, "error": PENDING_ERROR}
            call = self._calls.get(call_id)
            if call is None or call[1]:
                raise StateError("call is not bound to an active customer")
            self._tools[(call_id, tool_id)] = (fingerprint, None)
        return None

    def finish_tool(self, call_id: str, tool_id: str, response: dict) -> None:
        with self._lock:
            row = self._tools.get((call_id, tool_id))
            if row is not None:
                self._tools[(call_id, tool_id)] = (row[0], json.loads(json.dumps(response)))
