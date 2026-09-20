"""The shared Recall client and the per-caller view built on it."""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, TypeVar

from polign_recall import Belief, Client, RecallError, RememberResult

logger = logging.getLogger("recall_livekit")

T = TypeVar("T")

_MISSING: Any = object()


def _describe(exc: BaseException) -> str:
    text = str(exc)
    return f"{type(exc).__name__}: {text}" if text else type(exc).__name__


@dataclass(frozen=True)
class PredicateSpec:
    """One entry of the Recall predicate registry."""

    name: str
    cardinality: str  # "single" or "multi"
    value_type: str  # "string", "number", or "boolean"
    description: str


class RecallMemory:
    """One Recall client for a worker process.

    Create it once in the server's ``setup_fnc`` (LiveKit calls that once per
    process) and keep it in ``JobProcess.userdata``. Every ``polign_recall.Client``
    is a ``polign mcp`` subprocess, so one per turn or per session is wasteful.
    Calls are serialized on that subprocess; the local read path is a few
    milliseconds, so a handful of concurrent sessions per process share it well.
    """

    def __init__(self, client: Client) -> None:
        self._client = client
        self._registry: dict[str, PredicateSpec] = {}
        for entry in client.predicates():
            spec = PredicateSpec(
                name=entry["predicate"],
                cardinality=entry.get("cardinality", "single"),
                value_type=entry.get("value_type") or "string",
                description=entry.get("description", ""),
            )
            self._registry[spec.name] = spec

    @classmethod
    def open(
        cls,
        *,
        url: str | None = None,
        api_key: str | None = None,
        collection: str | None = None,
        predicates: str | None = None,
        local_dir: str | os.PathLike[str] | None = None,
        command: Sequence[str] | None = None,
        env: Mapping[str, str] | None = None,
        timeout: float = 30.0,
        write: bool = True,
    ) -> RecallMemory:
        """Start the Recall subprocess and read its predicate registry.

        ``local_dir`` keeps the database on this machine, in that directory:
        the first worker process to open it starts a background
        ``polign-server`` and the others share it. Use ``url`` and ``api_key``
        instead for a server you run yourself; the two are exclusive.

        ``url``, ``api_key``, ``collection`` and ``predicates`` (a registry file
        path) become the ``POLIGN_URL``, ``POLIGN_API_KEY``, ``POLIGN_COLLECTION``
        and ``POLIGN_PREDICATES`` variables of the subprocess; anything left
        unset falls through to the worker's environment. ``command`` replaces
        the default ``polign mcp -memory-only -write`` argv; the default runs
        the ``polign`` binary that pip installed with this package.
        """
        if local_dir is not None and (url is not None or api_key is not None):
            raise ValueError("local_dir runs its own server; do not pass url or api_key with it")
        merged = dict(env or {})
        for key, value in (
            ("POLIGN_URL", url),
            ("POLIGN_API_KEY", api_key),
            ("POLIGN_COLLECTION", collection),
            ("POLIGN_PREDICATES", predicates),
        ):
            if value is not None:
                merged[key] = value
        client = Client(command=command, env=merged, timeout=timeout, write=write, local_dir=local_dir)
        try:
            return cls(client)
        except BaseException:
            client.close()
            raise

    @property
    def client(self) -> Client:
        return self._client

    @property
    def registry(self) -> Mapping[str, PredicateSpec]:
        """The closed set of predicates this Recall instance accepts."""
        return self._registry

    def for_subject(
        self,
        subject: str,
        *,
        limit: int = 20,
        read_timeout: float = 0.5,
        write_timeout: float = 5.0,
    ) -> SubjectMemory:
        """A view of one caller's memory. ``subject`` should be a stable, auth-derived
        identifier such as the participant identity, never the room name."""
        if not subject or not subject.strip():
            raise ValueError("subject must be a non-empty string")
        return SubjectMemory(
            self, subject, limit=limit, read_timeout=read_timeout, write_timeout=write_timeout
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> RecallMemory:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


class SubjectMemory:
    """One caller's beliefs, with the async helpers the agent hooks need.

    Reads fail open: a slow or unavailable Recall returns the last loaded
    beliefs (or none) and logs a warning, so the voice session keeps going.
    Writes raise, so the tool can tell the model the fact was not saved.
    """

    def __init__(
        self,
        memory: RecallMemory,
        subject: str,
        *,
        limit: int,
        read_timeout: float,
        write_timeout: float,
    ) -> None:
        if limit < 1:
            raise ValueError("limit must be at least 1")
        self._memory = memory
        self.subject = subject
        self.limit = limit
        self.read_timeout = read_timeout
        self.write_timeout = write_timeout
        self.beliefs: list[Belief] = []
        self.loaded = False

    @property
    def registry(self) -> Mapping[str, PredicateSpec]:
        return self._memory.registry

    @property
    def overflowed(self) -> bool:
        """True when the last load hit the cap, so beliefs may be missing from
        the prompt and a per-turn search is worth running."""
        return len(self.beliefs) >= self.limit

    async def load(self) -> list[Belief]:
        """Every current belief about the subject, up to ``limit``."""
        client = self._memory.client
        try:
            beliefs = await self._call(
                lambda: client.recall(subject=self.subject, limit=self.limit), self.read_timeout
            )
        except (RecallError, asyncio.TimeoutError) as exc:
            logger.warning("recall load failed for subject %r: %s", self.subject, _describe(exc))
            return self.beliefs
        self.beliefs = list(beliefs)
        self.loaded = True
        return self.beliefs

    async def search(self, query: str, *, k: int = 5) -> list[Belief]:
        """Beliefs about the subject that match ``query``. Uses the embedder the
        Recall subprocess was started with; the default is word overlap."""
        client = self._memory.client
        try:
            hits = await self._call(
                lambda: client.recall(subject=self.subject, query=query, limit=k),
                self.read_timeout,
            )
        except (RecallError, asyncio.TimeoutError) as exc:
            logger.warning("recall search failed for subject %r: %s", self.subject, _describe(exc))
            return []
        return list(hits)

    async def remember(self, predicate: str, value: Any, *, source: str | None = None) -> RememberResult:
        client = self._memory.client
        result = await self._call(
            lambda: client.remember(self.subject, predicate, value, source=source),
            self.write_timeout,
        )
        if not isinstance(result, RememberResult):  # pragma: no cover - typed path only
            raise RecallError("unexpected extraction result from a typed remember")
        return result

    async def forget(self, predicate: str, value: Any = _MISSING, *, all: bool = False) -> int:
        client = self._memory.client
        if value is _MISSING:
            return await self._call(
                lambda: client.forget(self.subject, predicate, all=all), self.write_timeout
            )
        return await self._call(
            lambda: client.forget(self.subject, predicate, value), self.write_timeout
        )

    def coerce(self, predicate: str, value: Any) -> Any:
        """Turn a model-supplied value into the registry's type for ``predicate``."""
        spec = self.registry.get(predicate)
        if spec is None:
            raise ValueError(f"unknown memory type {predicate!r}")
        if spec.value_type == "number":
            if isinstance(value, bool):
                raise ValueError(f"{predicate} takes a number")
            if isinstance(value, (int, float)):
                return value
            text = str(value).strip().replace(",", "")
            try:
                number = float(text)
            except ValueError as exc:
                raise ValueError(f"{predicate} takes a number, got {value!r}") from exc
            return int(number) if number.is_integer() else number
        if spec.value_type == "boolean":
            if isinstance(value, bool):
                return value
            text = str(value).strip().lower()
            if text in ("true", "yes", "y", "1", "on"):
                return True
            if text in ("false", "no", "n", "0", "off"):
                return False
            raise ValueError(f"{predicate} takes yes or no, got {value!r}")
        text = str(value).strip()
        if not text:
            raise ValueError(f"{predicate} needs a value")
        return text

    async def _call(self, fn: Callable[[], T], timeout: float) -> T:
        # The Recall client is blocking; a thread keeps the audio pipeline
        # responsive. On timeout the thread finishes on its own later.
        return await asyncio.wait_for(asyncio.to_thread(fn), timeout)
