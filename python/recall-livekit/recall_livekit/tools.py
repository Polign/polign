"""Function tools that let the voice model write memory."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from livekit.agents import llm
from polign_recall import RecallError

from .memory import SubjectMemory

if TYPE_CHECKING:
    from .resume import AgentResume

OnChange = Callable[[], Awaitable[None]] | None


def _predicate_property(memory: SubjectMemory) -> dict[str, Any]:
    names = list(memory.registry)
    described = "; ".join(
        f"{spec.name}: {spec.description or spec.name}"
        + (" (can hold several values)" if spec.cardinality == "multi" else "")
        for spec in memory.registry.values()
    )
    return {
        "type": "string",
        "enum": names,
        "description": f"The kind of fact. One of: {described}.",
    }


def build_remember_tool(memory: SubjectMemory, *, on_change: OnChange = None) -> llm.RawFunctionTool:
    """A ``remember`` tool whose predicate list is the Recall registry.

    ``on_change`` runs after a successful write, so the agent can reload its
    memory block before the next turn.
    """
    schema = {
        "name": "remember",
        "description": (
            "Save a lasting fact the caller stated about themselves, such as their name, "
            "a preference, or an open issue. Call it once per fact, as soon as the fact is "
            "clear. Storing a new value for a single-valued kind replaces the old one."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "predicate": _predicate_property(memory),
                "value": {
                    "type": "string",
                    "description": "The fact itself, short and in the caller's words. "
                    "Numbers as digits, yes/no facts as true or false.",
                },
            },
            "required": ["predicate", "value"],
            "additionalProperties": False,
        },
    }

    async def remember(raw_arguments: dict[str, object]) -> str:
        predicate = str(raw_arguments.get("predicate", "")).strip()
        if predicate not in memory.registry:
            raise llm.ToolError(
                f"Unknown memory kind {predicate!r}. Use one of: {', '.join(memory.registry)}."
            )
        try:
            value = memory.coerce(predicate, raw_arguments.get("value"))
        except ValueError as exc:
            raise llm.ToolError(str(exc)) from exc
        try:
            result = await memory.remember(predicate, value)
        except RecallError as exc:
            raise llm.ToolError(f"The fact was not saved: {exc}") from exc
        except asyncio.TimeoutError as exc:
            raise llm.ToolError("Memory is slow right now; the fact was not saved.") from exc
        if on_change is not None:
            await on_change()
        if result.already_known:
            return f"Already remembered: {predicate} is {value}."
        if result.superseded:
            old = ", ".join(str(b.value) for b in result.superseded)
            return f"Updated {predicate} to {value}; it was {old}."
        return f"Remembered {predicate}: {value}."

    return llm.function_tool(remember, raw_schema=schema)


def build_forget_tool(memory: SubjectMemory, *, on_change: OnChange = None) -> llm.RawFunctionTool:
    """A ``forget`` tool. Off by default in ``RecallAgent``; enable it when the
    caller should be able to withdraw a fact by asking."""
    schema = {
        "name": "forget",
        "description": (
            "Withdraw a remembered fact when the caller asks you to forget it. "
            "Give the exact value to withdraw, or set everything to true to withdraw every "
            "value of that kind. The record of the change is kept."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "predicate": _predicate_property(memory),
                "value": {"type": "string", "description": "The value to withdraw."},
                "everything": {
                    "type": "boolean",
                    "description": "Withdraw every value of this kind instead of one.",
                },
            },
            "required": ["predicate"],
            "additionalProperties": False,
        },
    }

    async def forget(raw_arguments: dict[str, object]) -> str:
        predicate = str(raw_arguments.get("predicate", "")).strip()
        if predicate not in memory.registry:
            raise llm.ToolError(
                f"Unknown memory kind {predicate!r}. Use one of: {', '.join(memory.registry)}."
            )
        everything = bool(raw_arguments.get("everything", False))
        raw_value = raw_arguments.get("value")
        if not everything and (raw_value is None or str(raw_value).strip() == ""):
            raise llm.ToolError("Give the value to withdraw, or set everything to true.")
        try:
            if everything:
                count = await memory.forget(predicate, all=True)
            else:
                count = await memory.forget(predicate, memory.coerce(predicate, raw_value))
        except ValueError as exc:
            raise llm.ToolError(str(exc)) from exc
        except RecallError as exc:
            raise llm.ToolError(f"Nothing was withdrawn: {exc}") from exc
        except asyncio.TimeoutError as exc:
            raise llm.ToolError("Memory is slow right now; nothing was withdrawn.") from exc
        if on_change is not None:
            await on_change()
        if count == 0:
            return f"There was no remembered {predicate} to withdraw."
        return f"Withdrew {count} remembered value(s) of {predicate}."

    return llm.function_tool(forget, raw_schema=schema)


def build_working_state_tool(resume: AgentResume) -> llm.RawFunctionTool:
    """An ``update_working_state`` tool: the note a resumed agent continues
    from if the call drops and a new worker picks it up."""
    strings = {"type": "array", "items": {"type": "string"}}
    schema = {
        "name": "update_working_state",
        "description": (
            "Save your working state for this call: what the caller needs, what is done, and "
            "what you are doing now. If the call drops and continues on a new connection, this "
            "note is what you pick up from. Fields you leave out are kept. Call it when "
            "something changes, not on every turn."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "goal": {"type": "string", "description": "What the caller needs from this call."},
                "progress": {"type": "string", "description": "What is done so far."},
                "focus": {"type": "string", "description": "What you are doing right now."},
                "plan": {**strings, "description": "The remaining steps, in order."},
                "decisions": {**strings, "description": "Decisions made, each with its reason."},
                "open_questions": {**strings, "description": "What is still unknown."},
                "notes": {"type": "string", "description": "Anything else to carry over."},
            },
            "additionalProperties": False,
        },
    }
    allowed = set(schema["parameters"]["properties"])

    async def update_working_state(raw_arguments: dict[str, object]) -> str:
        fields = {k: v for k, v in raw_arguments.items() if k in allowed and v not in (None, "", [])}
        if not fields:
            raise llm.ToolError("Give at least one field to save.")
        try:
            state = await resume.update_working_state(**fields)
        except RecallError as exc:
            raise llm.ToolError(f"The working state was not saved: {exc}") from exc
        except asyncio.TimeoutError as exc:
            raise llm.ToolError("Memory is slow right now; the working state was not saved.") from exc
        return f"Working state saved (version {state.version})."

    return llm.function_tool(update_working_state, raw_schema=schema)
