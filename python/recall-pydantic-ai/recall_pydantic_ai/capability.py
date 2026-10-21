"""The RecallMemory capability: remembered facts in the instructions, and the
tools that let the model keep them current."""

from __future__ import annotations

import asyncio
import copy
import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, TypeVar

from polign_recall import Belief, Client, Memory, RecallError, RememberResult, TextResult
from pydantic_ai import ModelRetry, RunContext, Tool
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.exceptions import ToolFailed
from pydantic_ai.tools import AgentDepsT
from pydantic_ai.toolsets import AgentToolset, FunctionToolset

from .render import DEFAULT_TEMPLATE, PredicateSpec, memory_lines, render_beliefs, render_lines, render_memories

logger = logging.getLogger("recall_pydantic_ai")

T = TypeVar("T")
R = TypeVar("R", Belief, Memory)


def _describe(exc: BaseException) -> str:
    text = str(exc)
    return f"{type(exc).__name__}: {text}" if text else type(exc).__name__


def _prompt_text(prompt: Any) -> str:
    """The text parts of a run's user prompt."""
    if isinstance(prompt, str):
        return prompt
    if prompt is None:
        return ""
    return " ".join(part for part in prompt if isinstance(part, str))


def is_text(client: Client) -> bool:
    """True when ``client`` serves Recall's text tools rather than the typed ones.

    polign_recall decides this when it connects, from the tools the server
    lists, and keeps the answer on the client.
    """
    return bool(getattr(client, "_text", False))


def load_registry(client: Client) -> dict[str, PredicateSpec]:
    """The closed set of predicates the Recall server accepts."""
    registry: dict[str, PredicateSpec] = {}
    for entry in client.predicates():
        spec = PredicateSpec(
            name=entry["predicate"],
            cardinality=entry.get("cardinality", "single"),
            value_type=entry.get("value_type") or "string",
            description=entry.get("description", ""),
        )
        registry[spec.name] = spec
    return registry


def coerce(registry: Mapping[str, PredicateSpec], predicate: str, value: Any) -> Any:
    """Turn a model-supplied value into the registry's type for ``predicate``."""
    spec = registry.get(predicate)
    if spec is None:
        raise ValueError(f"Unknown memory kind {predicate!r}. Use one of: {', '.join(registry)}.")
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
    text = "" if value is None else str(value).strip()
    if not text:
        raise ValueError(f"{predicate} needs a value")
    return text


@dataclass
class RecallMemory(AbstractCapability[AgentDepsT]):
    """Long-term memory for an agent, backed by Recall by Polign.

    At the start of each run it loads what is remembered (up to ``limit``)
    and adds it to the instructions. The model gets a ``remember`` tool; a
    newer statement replaces an older one about the same thing, and the
    change is kept in its history. The next run sees the updated facts.

    ``client`` is a ``polign_recall.Client``. Create one per process and
    share it: each one is a ``recall mcp`` subprocess or an HTTP session. The
    capability follows the tools the client serves:

    - Text tools (the default, ``Client(...)``): ``remember`` takes a passage
      in the user's words and the server works out the facts with its
      extraction model (``extract_model``). Without one, text is kept as
      written and replaces nothing. The text tools keep one person's memory
      per collection, so ``subject`` is not taken; serve each user from their
      own collection, or use the typed tools.
    - Typed tools (``Client(..., typed=True)``): ``remember`` takes a
      predicate from the Recall registry and a value, for one ``subject`` per
      run. ``subject`` is a fixed string or a function of the run context,
      such as ``lambda ctx: ctx.deps.user_id``. Use a stable, auth-derived
      identifier.

    Reads fail open: if Recall is slow or down, the run goes on with no
    memory block and a warning is logged. Writes report the failure to the
    model as the tool's result.
    """

    client: Client
    subject: str | Callable[[RunContext[AgentDepsT]], str] | None = None
    limit: int = 20
    who: str = "the user"
    template: str = DEFAULT_TEMPLATE
    remember_tool: bool = True
    forget_tool: bool = False
    search_when_overflowed: bool = True
    read_timeout: float = 2.0
    write_timeout: float = 10.0

    text: bool = field(init=False)
    """True when the client serves the text tools."""
    registry: dict[str, PredicateSpec] = field(init=False, repr=False)
    """The predicate registry; empty with the text tools."""
    in_run: bool = field(init=False, default=False)
    """True on the copy made for a run."""
    run_subject: str | None = field(init=False, default=None)
    """The subject of the current run; None outside a run and with the text tools."""
    beliefs: list[Belief] = field(init=False, default_factory=list, repr=False)
    """What the current run loaded about the subject (typed tools)."""
    matches: list[Belief] = field(init=False, default_factory=list, repr=False)
    """Beliefs found by searching for the prompt, when ``beliefs`` hit ``limit``."""
    memories: list[Memory] = field(init=False, default_factory=list, repr=False)
    """What the current run loaded (text tools)."""
    memory_matches: list[Memory] = field(init=False, default_factory=list, repr=False)
    """Memories found by asking with the prompt, when there were more than ``limit``."""
    _more: bool = field(init=False, default=False, repr=False)

    def __post_init__(self) -> None:
        if self.limit < 1:
            raise ValueError("limit must be at least 1")
        if "{context}" not in self.template:
            raise ValueError("context template must contain a literal {context} placeholder")
        self.text = is_text(self.client)
        if self.text:
            if self.subject is not None:
                raise ValueError(
                    "this client serves Recall's text tools, which keep one person's memory per "
                    "collection and take no subject; drop subject, or open the client with typed=True"
                )
            self.registry = {}
            return
        if self.subject is None:
            raise ValueError("the typed tools need a subject: a string or a function of the run context")
        if isinstance(self.subject, str) and not self.subject.strip():
            raise ValueError("subject must be a non-empty string")
        self.registry = load_registry(self.client)

    @classmethod
    def get_serialization_name(cls) -> str | None:
        return None  # takes a live client

    @property
    def overflowed(self) -> bool:
        """True when this run's load hit ``limit``, so facts may be missing
        from the instructions and searching is worth it."""
        if self.text:
            return self._more
        return len(self.beliefs) >= self.limit

    async def for_run(self, ctx: RunContext[AgentDepsT]) -> RecallMemory[AgentDepsT]:
        if self.text:
            return await self._text_run(ctx)
        subject = self.subject(ctx) if callable(self.subject) else self.subject
        if not isinstance(subject, str) or not subject.strip():
            raise ValueError(f"RecallMemory subject must be a non-empty string, got {subject!r}")
        run = copy.copy(self)
        run.in_run = True
        run.run_subject = subject
        run.beliefs = await run._read(
            lambda: self.client.recall(subject=subject, limit=self.limit), "load"
        )
        run.matches = []
        query = _prompt_text(ctx.prompt).strip()
        if self.search_when_overflowed and run.overflowed and query:
            hits = await run._read(lambda: self.client.recall(subject=subject, query=query, limit=5), "search")
            shown = {(b.predicate, str(b.value)) for b in run.beliefs}
            run.matches = [b for b in hits if (b.predicate, str(b.value)) not in shown]
        return run

    async def _text_run(self, ctx: RunContext[AgentDepsT]) -> RecallMemory[AgentDepsT]:
        run = copy.copy(self)
        run.in_run = True
        # The profile has no limit argument; it is cut here.
        profile = await run._read(lambda: self.client.ask(), "load")
        run.memories = profile[: self.limit]
        run._more = len(profile) > self.limit
        run.memory_matches = []
        query = _prompt_text(ctx.prompt).strip()
        if self.search_when_overflowed and run._more and query:
            hits = await run._read(lambda: self.client.ask(query), "search")
            shown = {m.text for m in run.memories}
            run.memory_matches = [m for m in hits if m.text not in shown][:5]
        return run

    def get_instructions(self) -> str | None:
        if not self.in_run:
            return None
        if self.text:
            return render_memories(
                self.memories,
                template=self.template,
                who=self.who,
                remember_tool=self.remember_tool,
                matches=self.memory_matches,
            )
        return render_beliefs(
            self.beliefs,
            registry=self.registry,
            template=self.template,
            who=self.who,
            remember_tool=self.remember_tool,
            matches=self.matches,
        )

    def get_toolset(self) -> AgentToolset[AgentDepsT] | None:
        tools: list[Tool[AgentDepsT]] = []
        if self.remember_tool:
            tools.append(self._remember_text_tool() if self.text else self._remember_tool())
        if self.forget_tool:
            tools.append(self._forget_text_tool() if self.text else self._forget_tool())
        if not self.in_run or self.overflowed:
            tools.append(self._search_text_tool() if self.text else self._search_tool())
        return FunctionToolset(tools) if tools else None

    # --- tools ---

    def _predicate_schema(self) -> dict[str, Any]:
        described = "; ".join(
            f"{spec.name}: {spec.description or spec.name}"
            + (" (can hold several values)" if spec.cardinality == "multi" else "")
            for spec in self.registry.values()
        )
        return {"type": "string", "enum": list(self.registry), "description": f"The kind of fact. One of: {described}."}

    def _remember_tool(self) -> Tool[AgentDepsT]:
        async def remember(predicate: str, value: Any) -> str:
            predicate = str(predicate).strip()
            try:
                typed = coerce(self.registry, predicate, value)
            except ValueError as exc:
                raise ModelRetry(str(exc)) from exc
            result = await self._write(
                lambda: self.client.remember(self._subject(), predicate, typed), "The fact was not saved"
            )
            if not isinstance(result, RememberResult):  # pragma: no cover - typed path only
                raise ToolFailed("The fact was not saved: unexpected result from Recall.")
            if result.already_known:
                return f"Already remembered: {predicate} is {typed}."
            if result.superseded:
                old = ", ".join(str(b.value) for b in result.superseded)
                return f"Updated {predicate} to {typed}; it was {old}."
            return f"Remembered {predicate}: {typed}."

        return Tool.from_schema(
            remember,
            name="remember",
            description=(
                f"Save a lasting fact {self.who} stated, such as their name, a preference, or a "
                "project constraint. Call it once per fact, as soon as the fact is clear. Storing a "
                "new value for a single-valued kind replaces the old one."
            ),
            json_schema={
                "type": "object",
                "properties": {
                    "predicate": self._predicate_schema(),
                    "value": {
                        "type": "string",
                        "description": "The fact itself, short and in their words. "
                        "Numbers as digits, yes/no facts as true or false.",
                    },
                },
                "required": ["predicate", "value"],
                "additionalProperties": False,
            },
        )

    def _forget_tool(self) -> Tool[AgentDepsT]:
        async def forget(predicate: str, value: Any = None, everything: bool = False) -> str:
            predicate = str(predicate).strip()
            if predicate not in self.registry:
                raise ModelRetry(f"Unknown memory kind {predicate!r}. Use one of: {', '.join(self.registry)}.")
            if everything:
                count = await self._write(
                    lambda: self.client.forget(self._subject(), predicate, all=True), "Nothing was withdrawn"
                )
            else:
                if value is None or str(value).strip() == "":
                    raise ModelRetry("Give the value to withdraw, or set everything to true.")
                try:
                    typed = coerce(self.registry, predicate, value)
                except ValueError as exc:
                    raise ModelRetry(str(exc)) from exc
                count = await self._write(
                    lambda: self.client.forget(self._subject(), predicate, typed), "Nothing was withdrawn"
                )
            if count == 0:
                return f"There was no remembered {predicate} to withdraw."
            return f"Withdrew {count} remembered value(s) of {predicate}."

        return Tool.from_schema(
            forget,
            name="forget",
            description=(
                f"Withdraw a remembered fact when {self.who} asks you to forget it. Give the exact "
                "value to withdraw, or set everything to true to withdraw every value of that kind. "
                "The record of the change is kept."
            ),
            json_schema={
                "type": "object",
                "properties": {
                    "predicate": self._predicate_schema(),
                    "value": {"type": "string", "description": "The value to withdraw."},
                    "everything": {"type": "boolean", "description": "Withdraw every value of this kind instead of one."},
                },
                "required": ["predicate"],
                "additionalProperties": False,
            },
        )

    def _search_tool(self) -> Tool[AgentDepsT]:
        async def search_memory(query: str) -> str:
            subject = self._subject()
            try:
                hits = await self._call(
                    lambda: self.client.recall(subject=subject, query=query, limit=10), self.read_timeout
                )
            except RecallError as exc:
                raise ToolFailed(f"Memory search failed: {exc}") from exc
            except asyncio.TimeoutError as exc:
                raise ToolFailed("Memory is slow right now; nothing was searched.") from exc
            lines = render_lines(hits, self.registry)
            return "\n".join(lines) if lines else "Nothing remembered matches."

        return Tool.from_schema(
            search_memory,
            name="search_memory",
            description=(
                f"Search what is remembered about {self.who} beyond the facts in your instructions. "
                "Use it when the request needs a fact you do not see there."
            ),
            json_schema={
                "type": "object",
                "properties": {"query": {"type": "string", "description": "What to look for, in a few words."}},
                "required": ["query"],
                "additionalProperties": False,
            },
        )

    # --- text tools ---

    def _remember_text_tool(self) -> Tool[AgentDepsT]:
        async def remember(text: str) -> str:
            text = str(text).strip()
            if not text:
                raise ModelRetry("Pass what to remember as text.")
            self._need_run()
            result = await self._write(lambda: self.client.remember(text=text), "Nothing was saved")
            if not isinstance(result, TextResult):  # pragma: no cover - text path only
                raise ToolFailed("Nothing was saved: unexpected result from Recall.")
            said: list[str] = []
            for item in result.remembered:
                if item.already_known:
                    said.append(f"Already remembered: {item.text}.")
                elif item.replaces:
                    said.append(f"Updated: {item.text}; it replaced {'; '.join(item.replaces)}.")
                else:
                    said.append(f"Remembered: {item.text}.")
            if said:
                return " ".join(said)
            if result.kept_as_text:
                return "Kept as written."
            return "Nothing new to remember."

        return Tool.from_schema(
            remember,
            name="remember",
            description=(
                f"Save something worth keeping about {self.who} or their work: a preference, a fact, "
                "a decision, or a correction. Pass it as text, in their words where you can. A newer "
                "statement replaces an older one about the same thing; restating a known fact changes nothing."
            ),
            json_schema={
                "type": "object",
                "properties": {
                    "text": {"type": "string", "maxLength": 32768, "description": "What to remember."},
                },
                "required": ["text"],
                "additionalProperties": False,
            },
        )

    def _forget_text_tool(self) -> Tool[AgentDepsT]:
        async def forget(text: str) -> str:
            text = str(text).strip()
            if not text:
                raise ModelRetry("Describe what to forget, such as \"my editor\".")
            self._need_run()
            gone = await self._write(lambda: self.client.forget_text(text), "Nothing was withdrawn")
            if not gone:
                return "Nothing remembered matched, so nothing was withdrawn."
            return "Withdrew: " + "; ".join(gone) + "."

        return Tool.from_schema(
            forget,
            name="forget",
            description=(
                f"Withdraw what a description names when {self.who} asks you to forget it, such as "
                "\"my editor\" or \"that I live in Lisbon\". The record of the change is kept."
            ),
            json_schema={
                "type": "object",
                "properties": {"text": {"type": "string", "description": "What to forget, described in words."}},
                "required": ["text"],
                "additionalProperties": False,
            },
        )

    def _search_text_tool(self) -> Tool[AgentDepsT]:
        async def search_memory(query: str) -> str:
            self._need_run()
            try:
                hits = await self._call(lambda: self.client.ask(query), self.read_timeout)
            except RecallError as exc:
                raise ToolFailed(f"Memory search failed: {exc}") from exc
            except asyncio.TimeoutError as exc:
                raise ToolFailed("Memory is slow right now; nothing was searched.") from exc
            lines = memory_lines(hits[:10])
            return "\n".join(lines) if lines else "Nothing remembered matches."

        return Tool.from_schema(
            search_memory,
            name="search_memory",
            description=(
                f"Search what is remembered about {self.who} beyond the facts in your instructions. "
                "Use it when the request needs a fact you do not see there."
            ),
            json_schema={
                "type": "object",
                "properties": {"query": {"type": "string", "description": "What to look for, in a few words."}},
                "required": ["query"],
                "additionalProperties": False,
            },
        )

    # --- calls to the blocking client ---

    def _need_run(self) -> None:
        if not self.in_run:  # pragma: no cover - tools only exist inside a run
            raise ToolFailed("Memory is not available outside an agent run.")

    def _subject(self) -> str:
        self._need_run()
        assert self.run_subject is not None
        return self.run_subject

    async def _call(self, fn: Callable[[], T], timeout: float) -> T:
        # The Recall client blocks; a thread keeps the event loop free. On
        # timeout the thread finishes on its own later.
        return await asyncio.wait_for(asyncio.to_thread(fn), timeout)

    async def _read(self, fn: Callable[[], list[R]], what: str) -> list[R]:
        try:
            return list(await self._call(fn, self.read_timeout))
        except (RecallError, asyncio.TimeoutError) as exc:
            logger.warning("recall %s failed for subject %r: %s", what, self.run_subject, _describe(exc))
            return []

    async def _write(self, fn: Callable[[], T], failure: str) -> T:
        try:
            return await self._call(fn, self.write_timeout)
        except RecallError as exc:
            raise ToolFailed(f"{failure}: {exc}") from exc
        except asyncio.TimeoutError as exc:
            raise ToolFailed(f"{failure}: memory is slow right now.") from exc
