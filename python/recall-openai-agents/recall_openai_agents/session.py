"""A Session for the OpenAI Agents SDK whose history is a Recall record."""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Mapping, Sequence
from typing import Any

from polign_recall import Client, ResumedAgent

from ._records import Records, agent_name, fetch_json, store_json

SESSION_PREFIX = "oa-"
ITEMS = "items"

_ROLES = {"user": "user", "assistant": "assistant", "system": "system", "developer": "system"}


class RecallSession:
    """Conversation history for an OpenAI Agents SDK run, kept in Recall.

    It implements the SDK's ``Session`` protocol: pass it as ``session=`` to
    ``Runner.run`` and every turn's items are stored and read back on the
    next run. Each session is a Recall agent, ``oa-<session_id>``, and the
    items are one record under it, written under the agent's lease (taken
    for the write and handed back after it) and read by any process::

        session = RecallSession("support-42", local_dir="./recall-data")
        result = await Runner.run(agent, "Hi, I am Sam.", session=session)
        result = await Runner.run(agent, "What is my name?", session=session)

    With ``record_turns`` (the default) each message is also recorded as a
    turn of the Recall agent, so ``agent_resume`` on ``oa-<session_id>``
    briefs another process with the conversation; ``pop_item`` and
    ``clear_session`` change what the SDK reads back, not that record.

    ``client`` is a ``polign_recall.Client`` opened with ``agent=True``;
    without one, a client is opened from ``local_dir``, ``backend``,
    ``backend_url``, ``backend_key`` and ``env`` and closed by ``close``.
    """

    session_settings = None

    def __init__(
        self,
        session_id: str,
        *,
        client: Client | None = None,
        local_dir: str | os.PathLike[str] | None = None,
        env: Mapping[str, str] | None = None,
        backend: str | None = None,
        backend_url: str | os.PathLike[str] | None = None,
        backend_key: str | None = None,
        record_turns: bool = True,
        lease_ttl: float = 10,
        lease_wait: float = 30,
    ) -> None:
        if not session_id:
            raise ValueError("session_id must not be empty")
        self.session_id = session_id
        self.agent_id = agent_name(SESSION_PREFIX, session_id)
        self.record_turns = record_turns
        self.records = Records(client=client, local_dir=local_dir, env=env, backend=backend,
                               backend_url=backend_url, backend_key=backend_key,
                               lease_ttl=lease_ttl, lease_wait=lease_wait)

    def close(self) -> None:
        self.records.close()

    def __enter__(self) -> RecallSession:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # The Session protocol.

    async def get_items(self, limit: int | None = None) -> list[Any]:
        return await asyncio.to_thread(self.get_items_sync, limit)

    async def add_items(self, items: list[Any]) -> None:
        await asyncio.to_thread(self.add_items_sync, items)

    async def pop_item(self) -> Any | None:
        return await asyncio.to_thread(self.pop_item_sync)

    async def clear_session(self) -> None:
        await asyncio.to_thread(self.clear_session_sync)

    # The same, blocking.

    def get_items_sync(self, limit: int | None = None) -> list[Any]:
        """The history, oldest first; with ``limit``, the latest that many."""
        if limit is not None and limit <= 0:
            return []
        doc = self.records.read(self.agent_id, lambda agent: fetch_json(agent, ITEMS)) or {"items": []}
        items = [item for _, item in doc["items"]]
        return items[-limit:] if limit else items

    def add_items_sync(self, items: Sequence[Any]) -> None:
        items = list(items)
        if not items:
            return

        def write(agent: ResumedAgent) -> None:
            doc = fetch_json(agent, ITEMS) or {"next": 1, "items": []}
            for item in items:
                seq = doc["next"]
                doc["items"].append([seq, item])
                doc["next"] = seq + 1
                if self.record_turns:
                    self._record(agent, seq, item)
            store_json(agent, ITEMS, doc, f"items of session {self.session_id}")

        self.records.write(self.agent_id, write)

    def pop_item_sync(self) -> Any | None:
        def write(agent: ResumedAgent) -> Any | None:
            doc = fetch_json(agent, ITEMS)
            if not doc or not doc["items"]:
                return None
            _, item = doc["items"].pop()
            store_json(agent, ITEMS, doc, f"items of session {self.session_id}")
            return item

        return self.records.write(self.agent_id, write)

    def clear_session_sync(self) -> None:
        def write(agent: ResumedAgent) -> None:
            doc = fetch_json(agent, ITEMS)
            if doc and doc["items"]:
                store_json(agent, ITEMS, {"next": doc["next"], "items": []}, f"items of session {self.session_id}")

        self.records.write(self.agent_id, write)

    # Turns.

    def _record(self, agent: ResumedAgent, seq: int, item: Any) -> None:
        turn = _turn(item)
        if turn is None:
            return
        role, content, name = turn
        agent.record_turn(role, content, name, message_id=f"{self.session_id}:{seq}")


def _turn(item: Any) -> tuple[str, str, str | None] | None:
    """The turn an input item records as: (role, content, name), or None for
    items that are not part of the conversation (reasoning, references)."""
    if not isinstance(item, Mapping):
        return None
    kind = item.get("type") or ("message" if "role" in item else None)
    if kind == "message":
        role = _ROLES.get(str(item.get("role")))
        text = _text(item.get("content"))
        if role is None or not text:
            return None
        return role, text, None
    if kind == "function_call":
        name = str(item.get("name") or "")
        arguments = item.get("arguments")
        if not isinstance(arguments, str):
            arguments = json.dumps(arguments, default=str)
        return "assistant", f"{name}({arguments})", name or None
    if kind == "function_call_output":
        output = item.get("output")
        text = output if isinstance(output, str) else _text(output) or json.dumps(output, default=str)
        return "tool", text, str(item.get("call_id") or "") or None
    return None


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, Sequence):
        parts = []
        for part in content:
            if isinstance(part, Mapping):
                text = part.get("text")
                if isinstance(text, str):
                    parts.append(text)
            elif isinstance(part, str):
                parts.append(part)
        return "\n".join(parts)
    return ""
