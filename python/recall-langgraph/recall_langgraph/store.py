"""A LangGraph store whose items are Recall records."""

from __future__ import annotations

import asyncio
import json
import os
import re
from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from typing import Any

from langgraph.store.base import (
    BaseStore,
    GetOp,
    Item,
    ListNamespacesOp,
    MatchCondition,
    Op,
    PutOp,
    Result,
    SearchItem,
    SearchOp,
    validate_op_namespace,
)
from polign_recall import Client, ResumedAgent

from ._records import Records, agent_name, fetch_json, ref_name, store_json

STORE_PREFIX = "lgs-"
NAMESPACES = "namespaces"


class RecallStore(BaseStore):
    """Keeps LangGraph store items in Recall.

    The store is one Recall agent, ``lgs-<name>``; each namespace is one
    record under it holding that namespace's items, and one more record
    lists the namespaces. Items are read by any process and written under
    the agent's lease, taken for the write and handed back after it.

    ``search`` filters the way ``InMemoryStore`` does (equality, nested
    values, ``$eq`` ``$ne`` ``$gt`` ``$gte`` ``$lt`` ``$lte``). A ``query``
    ranks items by how many of its words appear in the item, in this
    client; there is no embedding index, so ``index`` on ``put`` and the
    ``refresh_ttl`` flags are accepted and ignored.

    One namespace's items are one record, bounded by Recall's output size
    (1 MiB), so keep namespaces to what one agent or user holds rather than
    putting every item of an application in one.
    """

    def __init__(
        self,
        name: str = "default",
        *,
        client: Client | None = None,
        local_dir: str | os.PathLike[str] | None = None,
        env: Mapping[str, str] | None = None,
        backend: str | None = None,
        backend_url: str | os.PathLike[str] | None = None,
        backend_key: str | None = None,
        lease_ttl: float = 10,
        lease_wait: float = 30,
    ) -> None:
        super().__init__()
        self.name = name
        self.agent_id = agent_name(STORE_PREFIX, name)
        self.records = Records(client=client, local_dir=local_dir, env=env, backend=backend,
                               backend_url=backend_url, backend_key=backend_key,
                               lease_ttl=lease_ttl, lease_wait=lease_wait)

    def close(self) -> None:
        self.records.close()

    def __enter__(self) -> RecallStore:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def batch(self, ops: Iterable[Op]) -> list[Result]:
        ops = list(ops)
        for op in ops:
            validate_op_namespace(op)
        results: list[Result] = []
        puts = [op for op in ops if isinstance(op, PutOp)]
        if puts:
            self.records.write(self.agent_id, lambda agent: self._apply_puts(agent, puts))
        if all(isinstance(op, PutOp) for op in ops):
            return [None] * len(ops)
        return self.records.read(self.agent_id, lambda agent: [self._answer(agent, op) for op in ops])

    def _answer(self, agent: ResumedAgent, op: Op) -> Result:
        if isinstance(op, PutOp):
            return None
        if isinstance(op, GetOp):
            return self._get(agent, op)
        if isinstance(op, SearchOp):
            return self._search(agent, op)
        if isinstance(op, ListNamespacesOp):
            return self._list_namespaces(agent, op)
        raise ValueError(f"Unknown operation type: {type(op)}")

    async def abatch(self, ops: Iterable[Op]) -> list[Result]:
        ops = list(ops)
        return await asyncio.to_thread(self.batch, ops)

    # Records.

    @staticmethod
    def _ref(namespace: tuple[str, ...]) -> str:
        return ref_name("n-", *namespace)

    def _namespaces(self, agent: ResumedAgent) -> dict[str, list[str]]:
        return (fetch_json(agent, NAMESPACES) or {}).get("namespaces", {})

    def _items(self, agent: ResumedAgent, namespace: tuple[str, ...]) -> dict[str, dict[str, Any]]:
        return (fetch_json(agent, self._ref(namespace)) or {}).get("items", {})

    def _apply_puts(self, agent: ResumedAgent, puts: list[PutOp]) -> None:
        namespaces = self._namespaces(agent)
        new = False
        by_namespace: dict[tuple[str, ...], list[PutOp]] = {}
        for op in puts:
            by_namespace.setdefault(tuple(op.namespace), []).append(op)
        for namespace, group in by_namespace.items():
            ref = self._ref(namespace)
            items = self._items(agent, namespace)
            now = datetime.now(timezone.utc).isoformat()
            for op in group:
                if op.value is None:
                    items.pop(op.key, None)
                    continue
                created = items.get(op.key, {}).get("created_at", now)
                items[op.key] = {"value": dict(op.value), "created_at": created, "updated_at": now}
            store_json(agent, ref, {"namespace": list(namespace), "items": items},
                       "store namespace " + "/".join(namespace))
            if ref not in namespaces:
                namespaces[ref] = list(namespace)
                new = True
        if new:
            store_json(agent, NAMESPACES, {"namespaces": namespaces}, "store namespaces")

    def _get(self, agent: ResumedAgent, op: GetOp) -> Item | None:
        stored = self._items(agent, tuple(op.namespace)).get(op.key)
        if stored is None:
            return None
        return _item(tuple(op.namespace), op.key, stored)

    def _search(self, agent: ResumedAgent, op: SearchOp) -> list[SearchItem]:
        prefix = tuple(op.namespace_prefix)
        found: list[tuple[float | None, Item]] = []
        for namespace in self._namespaces(agent).values():
            namespace = tuple(namespace)
            if namespace[:len(prefix)] != prefix:
                continue
            for key, stored in self._items(agent, namespace).items():
                item = _item(namespace, key, stored)
                if op.filter and not all(_compare_values(item.value.get(k), v) for k, v in op.filter.items()):
                    continue
                found.append((_score(op.query, item.value) if op.query else None, item))
        if op.query:
            found.sort(key=lambda pair: pair[0] or 0.0, reverse=True)
        page = found[op.offset:op.offset + op.limit]
        return [SearchItem(namespace=item.namespace, key=item.key, value=item.value, created_at=item.created_at,
                           updated_at=item.updated_at, score=score) for score, item in page]

    def _list_namespaces(self, agent: ResumedAgent, op: ListNamespacesOp) -> list[tuple[str, ...]]:
        namespaces = [tuple(ns) for ns in self._namespaces(agent).values()]
        if op.match_conditions:
            namespaces = [ns for ns in namespaces if all(_does_match(c, ns) for c in op.match_conditions)]
        if op.max_depth is not None:
            namespaces = sorted({ns[:op.max_depth] for ns in namespaces})
        else:
            namespaces = sorted(namespaces)
        return namespaces[op.offset:op.offset + op.limit]


def _item(namespace: tuple[str, ...], key: str, stored: dict[str, Any]) -> Item:
    return Item(value=stored["value"], key=key, namespace=namespace,
                created_at=datetime.fromisoformat(stored["created_at"]),
                updated_at=datetime.fromisoformat(stored["updated_at"]))


_WORD = re.compile(r"\w+")


def _score(query: str, value: Mapping[str, Any]) -> float:
    """The share of the query's words found in the item, in any field."""
    words = {w.lower() for w in _WORD.findall(query)}
    if not words:
        return 0.0
    text = {w.lower() for w in _WORD.findall(json.dumps(value, default=str))}
    return len(words & text) / len(words)


def _does_match(condition: MatchCondition, key: tuple[str, ...]) -> bool:
    path = tuple(condition.path)
    if len(key) < len(path):
        return False
    pairs = zip(key, path) if condition.match_type == "prefix" else zip(reversed(key), reversed(path))
    return all(p == "*" or k == p for k, p in pairs)


def _compare_values(item_value: Any, filter_value: Any) -> bool:
    if isinstance(filter_value, dict):
        if any(k.startswith("$") for k in filter_value):
            return all(_apply_operator(item_value, k, v) for k, v in filter_value.items())
        if not isinstance(item_value, dict):
            return False
        return all(_compare_values(item_value.get(k), v) for k, v in filter_value.items())
    if isinstance(filter_value, (list, tuple)):
        return (isinstance(item_value, (list, tuple)) and len(item_value) == len(filter_value)
                and all(_compare_values(a, b) for a, b in zip(item_value, filter_value)))
    return item_value == filter_value


def _apply_operator(value: Any, operator: str, op_value: Any) -> bool:
    if operator == "$eq":
        return value == op_value
    if operator == "$ne":
        return value != op_value
    if operator == "$gt":
        return float(value) > float(op_value)
    if operator == "$gte":
        return float(value) >= float(op_value)
    if operator == "$lt":
        return float(value) < float(op_value)
    if operator == "$lte":
        return float(value) <= float(op_value)
    raise ValueError(f"Unsupported operator: {operator}")
