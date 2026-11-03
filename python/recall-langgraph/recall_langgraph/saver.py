"""A LangGraph checkpointer whose checkpoints are Recall records."""

from __future__ import annotations

import asyncio
import base64
import os
import random
from collections.abc import AsyncIterator, Iterator, Mapping, Sequence
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import (
    WRITES_IDX_MAP,
    BaseCheckpointSaver,
    ChannelVersions,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
    PendingWrite,
    SerializerProtocol,
    get_checkpoint_id,
    get_checkpoint_metadata,
)
from polign_recall import Client, ResumedAgent

from ._records import Records, agent_name, fetch_json, ref_name, store_json

#: Every thread's checkpoints live under an agent named after it.
THREAD_PREFIX = "lg-"
INDEX = "index"


class RecallSaver(BaseCheckpointSaver[str]):
    """Keeps LangGraph checkpoints in Recall.

    Each thread is a Recall agent, ``lg-<thread_id>``, and its checkpoints,
    their metadata and their pending writes are records under it, written
    under the agent's lease and readable by any process. It passes LangGraph's
    checkpointer conformance suite for ``put``, ``put_writes``,
    ``get_tuple``, ``list`` and ``delete_thread``.

    Use it where you would use any checkpointer::

        with RecallSaver(local_dir="./recall-data") as saver:
            graph = builder.compile(checkpointer=saver)
            graph.invoke(state, {"configurable": {"thread_id": "t1"}})

    It is independent of ``RecallResume``: a graph can use both, the
    checkpointer for threads and interrupts, resume for the briefing a new
    process starts from. Their records never meet, since the saver's agents
    carry the ``lg-`` prefix.

    ``client`` is a ``polign_recall.Client`` opened with ``agent=True``;
    without one, a client is opened from ``local_dir``, ``backend``,
    ``backend_url``, ``backend_key`` and ``env`` and closed by ``close``.
    A write takes the thread's lease and hands it back when done, waiting up
    to ``lease_wait`` seconds for another process to finish first.

    Records are append-only. ``delete_thread`` drops the thread's index, so
    nothing of it is found again, but the stored records stay in the
    database. Listing across all threads (``list(None)``) is not supported.
    """

    def __init__(
        self,
        *,
        serde: SerializerProtocol | None = None,
        client: Client | None = None,
        local_dir: str | os.PathLike[str] | None = None,
        env: Mapping[str, str] | None = None,
        backend: str | None = None,
        backend_url: str | os.PathLike[str] | None = None,
        backend_key: str | None = None,
        lease_ttl: float = 10,
        lease_wait: float = 30,
    ) -> None:
        super().__init__(serde=serde)
        self.records = Records(client=client, local_dir=local_dir, env=env, backend=backend,
                               backend_url=backend_url, backend_key=backend_key,
                               lease_ttl=lease_ttl, lease_wait=lease_wait)

    def close(self) -> None:
        self.records.close()

    def __enter__(self) -> RecallSaver:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    async def __aenter__(self) -> RecallSaver:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await asyncio.to_thread(self.close)

    # Encoding.

    def _dump(self, value: Any) -> list[str]:
        kind, data = self.serde.dumps_typed(value)
        return [kind, base64.b64encode(data).decode()]

    def _load(self, packed: Sequence[str]) -> Any:
        return self.serde.loads_typed((packed[0], base64.b64decode(packed[1])))

    # Sync.

    def get_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        thread_id = config["configurable"]["thread_id"]
        ns = config["configurable"].get("checkpoint_ns", "")
        wanted = get_checkpoint_id(config)

        def read(agent: ResumedAgent) -> CheckpointTuple | None:
            index = fetch_json(agent, INDEX) or {}
            space = index.get("ns", {}).get(ns)
            if not space:
                return None
            checkpoint_id = max(space["checkpoints"]) if wanted is None else wanted
            if checkpoint_id not in space["checkpoints"]:
                return None
            return self._tuple(agent, thread_id, ns, checkpoint_id, space)

        return self.records.read(agent_name(THREAD_PREFIX, thread_id), read)

    def list(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> Iterator[CheckpointTuple]:
        if config is None:
            raise ValueError("RecallSaver lists one thread at a time; pass a config with a thread_id")
        thread_id = config["configurable"]["thread_id"]
        config_ns = config["configurable"].get("checkpoint_ns")
        config_id = get_checkpoint_id(config)
        before_id = get_checkpoint_id(before) if before else None

        def read(agent: ResumedAgent) -> list[CheckpointTuple]:
            index = fetch_json(agent, INDEX) or {}
            out: list[CheckpointTuple] = []
            for ns, space in sorted(index.get("ns", {}).items()):
                if config_ns is not None and ns != config_ns:
                    continue
                for checkpoint_id in sorted(space["checkpoints"], reverse=True):
                    if config_id and checkpoint_id != config_id:
                        continue
                    if before_id and checkpoint_id >= before_id:
                        continue
                    if limit is not None and len(out) >= limit:
                        return out
                    found = self._tuple(agent, thread_id, ns, checkpoint_id, space)
                    if found is None:
                        continue
                    if filter and not all(found.metadata.get(k) == v for k, v in filter.items()):
                        continue
                    out.append(found)
            return out

        yield from self.records.read(agent_name(THREAD_PREFIX, thread_id), read)

    def put(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        thread_id = config["configurable"]["thread_id"]
        ns = config["configurable"].get("checkpoint_ns", "")
        parent = config["configurable"].get("checkpoint_id")
        checkpoint_id = checkpoint["id"]
        record = {
            "id": checkpoint_id,
            "ns": ns,
            "parent": parent,
            "checkpoint": self._dump(checkpoint),
            "metadata": self._dump(get_checkpoint_metadata(config, metadata)),
        }

        def write(agent: ResumedAgent) -> None:
            store_json(agent, ref_name("c-", ns, checkpoint_id), record, f"checkpoint {checkpoint_id}")
            index = fetch_json(agent, INDEX) or {"thread_id": thread_id, "ns": {}}
            space = index["ns"].setdefault(ns, {"checkpoints": {}, "writes": {}})
            space["checkpoints"][checkpoint_id] = {"parent": parent}
            store_json(agent, INDEX, index, f"checkpoints of thread {thread_id}")

        self.records.write(agent_name(THREAD_PREFIX, thread_id), write)
        return {"configurable": {"thread_id": thread_id, "checkpoint_ns": ns, "checkpoint_id": checkpoint_id}}

    def put_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        thread_id = config["configurable"]["thread_id"]
        ns = config["configurable"].get("checkpoint_ns", "")
        checkpoint_id = config["configurable"]["checkpoint_id"]
        ref = ref_name("w-", ns, checkpoint_id, task_id)

        def write(agent: ResumedAgent) -> None:
            stored = fetch_json(agent, ref) or {"task_id": task_id, "task_path": task_path, "writes": {}}
            for idx, (channel, value) in enumerate(writes):
                key = str(WRITES_IDX_MAP.get(channel, idx))
                if not key.startswith("-") and key in stored["writes"]:
                    continue
                stored["writes"][key] = [channel, self._dump(value)]
            store_json(agent, ref, stored, f"writes of task {task_id}")
            index = fetch_json(agent, INDEX) or {"thread_id": thread_id, "ns": {}}
            space = index["ns"].setdefault(ns, {"checkpoints": {}, "writes": {}})
            tasks = space["writes"].setdefault(checkpoint_id, [])
            if task_id not in tasks:
                tasks.append(task_id)
                store_json(agent, INDEX, index, f"checkpoints of thread {thread_id}")

        self.records.write(agent_name(THREAD_PREFIX, thread_id), write)

    def delete_thread(self, thread_id: str) -> None:
        def write(agent: ResumedAgent) -> None:
            if fetch_json(agent, INDEX):
                store_json(agent, INDEX, {"thread_id": thread_id, "ns": {}}, f"checkpoints of thread {thread_id}")

        self.records.write(agent_name(THREAD_PREFIX, thread_id), write)

    def get_next_version(self, current: str | None, channel: None = None) -> str:
        if current is None:
            current_v = 0
        elif isinstance(current, int):
            current_v = current
        else:
            current_v = int(current.split(".")[0])
        return f"{current_v + 1:032}.{random.random():016}"

    # Async: the client is blocking, so each call runs in a thread.

    async def aget_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        return await asyncio.to_thread(self.get_tuple, config)

    async def alist(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> AsyncIterator[CheckpointTuple]:
        found = await asyncio.to_thread(
            lambda: list(self.list(config, filter=filter, before=before, limit=limit)))
        for item in found:
            yield item

    async def aput(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        return await asyncio.to_thread(self.put, config, checkpoint, metadata, new_versions)

    async def aput_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        await asyncio.to_thread(self.put_writes, config, writes, task_id, task_path)

    async def adelete_thread(self, thread_id: str) -> None:
        await asyncio.to_thread(self.delete_thread, thread_id)

    # Reading one checkpoint back.

    def _tuple(self, agent: ResumedAgent, thread_id: str, ns: str, checkpoint_id: str,
               space: dict[str, Any]) -> CheckpointTuple | None:
        record = fetch_json(agent, ref_name("c-", ns, checkpoint_id))
        if record is None:
            return None
        pending: list[PendingWrite] = []
        for task_id in space.get("writes", {}).get(checkpoint_id, []):
            stored = fetch_json(agent, ref_name("w-", ns, checkpoint_id, task_id))
            if not stored:
                continue
            for key, (channel, packed) in sorted(stored["writes"].items(), key=lambda kv: int(kv[0])):
                pending.append((task_id, channel, self._load(packed)))
        parent = record.get("parent")
        return CheckpointTuple(
            config={"configurable": {"thread_id": thread_id, "checkpoint_ns": ns, "checkpoint_id": checkpoint_id}},
            checkpoint=self._load(record["checkpoint"]),
            metadata=self._load(record["metadata"]),
            parent_config=(
                {"configurable": {"thread_id": thread_id, "checkpoint_ns": ns, "checkpoint_id": parent}}
                if parent else None
            ),
            pending_writes=pending,
        )
