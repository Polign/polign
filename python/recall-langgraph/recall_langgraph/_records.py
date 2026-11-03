"""Named records under Recall agents, for the checkpointer and the store.

Recall's State API keeps records per agent: outputs stored under a ref,
fenced by the agent's lease. ``Records`` uses that as a small document
store. Each document is one output under a fixed ref. Reads go through a
deferred resume, which takes no lease; a write takes the lease, runs, and
hands the lease back, so any process can write any agent in turn.

A client session holds one resumed agent per id, and a released agent is
closed, so the deferred agent is kept per id for reads and dropped after a
write has released it.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from collections.abc import Callable, Mapping
from typing import Any, TypeVar

from polign_recall import Client, RecallError, ResumedAgent

T = TypeVar("T")

_NAME = re.compile(r"^[A-Za-z0-9._-]{1,128}$")


def agent_name(prefix: str, raw: str) -> str:
    """An agent id for ``raw`` under ``prefix``: the raw name when Recall
    accepts it as is, else a hash of it."""
    name = f"{prefix}{raw}"
    if _NAME.match(name):
        return name
    return f"{prefix}{hashlib.sha256(raw.encode()).hexdigest()[:40]}"


def ref_name(prefix: str, *parts: str) -> str:
    """An output ref for ``parts``: hashed, since parts may hold any character."""
    return prefix + hashlib.sha256("\x1f".join(parts).encode()).hexdigest()[:40]


class Records:
    """Documents under Recall agents.

    ``client`` is a ``polign_recall.Client`` opened with ``agent=True``. When
    it is left out one is opened from ``local_dir``, ``backend``,
    ``backend_url``, ``backend_key`` and ``env``, the same names
    ``RecallResume`` takes, and closed by ``close``.

    ``lease_ttl`` is how long a write's lease lasts if the process dies in
    the middle of it; ``lease_wait`` is how long a write waits for another
    process to finish with the agent before giving up with "lease_held".
    """

    def __init__(
        self,
        *,
        client: Client | None = None,
        lease_ttl: float = 10,
        lease_wait: float = 30,
        local_dir: str | os.PathLike[str] | None = None,
        env: Mapping[str, str] | None = None,
        backend: str | None = None,
        backend_url: str | os.PathLike[str] | None = None,
        backend_key: str | None = None,
    ) -> None:
        options = {"local_dir": local_dir, "env": env, "backend": backend,
                   "backend_url": backend_url, "backend_key": backend_key}
        if client is not None and any(v is not None for v in options.values()):
            raise ValueError("local_dir, env, backend, backend_url and backend_key open a client; do not pass them with client")
        self._client = client
        self._owns_client = client is None
        self._options = {k: v for k, v in options.items() if v is not None}
        self.lease_ttl = lease_ttl
        self.lease_wait = lease_wait
        self._lock = threading.Lock()
        self._agent_locks: dict[str, threading.Lock] = {}
        self._agents: dict[str, ResumedAgent] = {}
        self._closed = False

    @property
    def client(self) -> Client:
        with self._lock:
            if self._closed:
                raise RuntimeError("closed")
            if self._client is None:
                self._client = Client(agent=True, **self._options)
            return self._client

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            client, self._client = self._client, None
            agents, self._agents = self._agents, {}
        for agent in agents.values():
            try:
                agent.release()
            except RecallError:
                pass
        if self._owns_client and client is not None:
            client.close()

    def _agent_lock(self, agent_id: str) -> threading.Lock:
        with self._lock:
            return self._agent_locks.setdefault(agent_id, threading.Lock())

    def _agent(self, agent_id: str) -> ResumedAgent:
        """The agent, resumed without its lease."""
        with self._lock:
            agent = self._agents.get(agent_id)
        if agent is None:
            agent = self.client.resume(agent_id, defer_lease=True, lease_ttl=self.lease_ttl)
            with self._lock:
                self._agents[agent_id] = agent
        return agent

    def read(self, agent_id: str, fn: Callable[[ResumedAgent], T]) -> T:
        """Run ``fn`` on the agent without its lease."""
        with self._agent_lock(agent_id):
            return fn(self._agent(agent_id))

    def write(self, agent_id: str, fn: Callable[[ResumedAgent], T]) -> T:
        """Run ``fn`` with the agent's lease held, then hand the lease back."""
        with self._agent_lock(agent_id):
            agent = self._agent(agent_id)
            self._acquire(agent)
            try:
                return fn(agent)
            finally:
                with self._lock:
                    self._agents.pop(agent_id, None)
                agent.release()

    def _acquire(self, agent: ResumedAgent) -> None:
        deadline = time.monotonic() + self.lease_wait
        delay = 0.05
        while not agent.acquire():
            if time.monotonic() >= deadline:
                raise RecallError(f"recall: agent {agent.agent_id!r} is held by another process", code="lease_held")
            time.sleep(delay)
            delay = min(delay * 2, 1.0)


def fetch(agent: ResumedAgent, ref: str) -> str | None:
    try:
        return agent.fetch_output(ref).content
    except RecallError as exc:
        if "has no output" in str(exc):
            return None
        raise


def fetch_json(agent: ResumedAgent, ref: str) -> Any:
    text = fetch(agent, ref)
    return None if text is None else json.loads(text)


def store_json(agent: ResumedAgent, ref: str, value: Any, summary: str) -> None:
    agent.store_output(json.dumps(value, separators=(",", ":")), ref=ref, summary=summary)
