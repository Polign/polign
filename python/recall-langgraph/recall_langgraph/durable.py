"""Tools that record each call as a Recall action, so a crash never leaves the
next process guessing whether a call happened."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Mapping, Sequence
from typing import TYPE_CHECKING, Any

from langchain_core.messages import ToolMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from langgraph.errors import GraphBubbleUp
from polign_recall import Action, ActionEnd

if TYPE_CHECKING:
    from .resume import RecallResume

#: The classes a tool can have. See ``RecallResume.durable``.
CLASSES = ("pure", "idempotent", "external")

#: A durable tool with an argument of this name gets the action's key in it,
#: the same key on every retry. An idempotent tool passes it to the outside
#: system so a repeat does nothing; an external tool records it with what it
#: does (a mail header, ticket metadata), so a resolver can look the call up
#: by key after a crash. Annotate it ``InjectedToolArg`` so the model is not
#: asked for it.
KEY_ARG = "idempotency_key"

#: Turns recorded for a tool's result use this message id, so the recorder can
#: tell the ToolMessage the graph adds afterwards from a new message.
TOOL_TURN_PREFIX = "tool-call:"

Resolver = Callable[[Action], "ActionEnd | None"]


def tool_turn_id(call_id: str) -> str:
    return TOOL_TURN_PREFIX + call_id


def takes_key(tool: BaseTool) -> bool:
    """Whether the tool has an idempotency_key argument. ``tool.args`` leaves
    out injected arguments, so read the full input schema."""
    try:
        return KEY_ARG in tool.get_input_schema().model_fields
    except Exception:
        return KEY_ARG in tool.args


class DurableTool(BaseTool):
    """A tool whose every call is bracketed by a Recall action: recorded
    before it runs, and closed with its result once it returns. Build these
    with ``RecallResume.durable``, not directly."""

    inner: BaseTool
    action_class: str
    resume: Any  # RecallResume; Any keeps pydantic from validating it

    def _run(self, *args: Any, **kwargs: Any) -> Any:  # pragma: no cover - invoke is overridden
        raise NotImplementedError("DurableTool runs through invoke")

    def invoke(self, input: Any, config: RunnableConfig | None = None, **kwargs: Any) -> Any:
        action, call_id, input = self._begin(input)
        try:
            out = self.inner.invoke(input, config, **kwargs)
        except Exception as exc:
            self._fail(action, exc)
            raise
        return self._end(action, call_id, out)

    async def ainvoke(self, input: Any, config: RunnableConfig | None = None, **kwargs: Any) -> Any:
        action, call_id, input = await asyncio.to_thread(self._begin, input)
        try:
            out = await self.inner.ainvoke(input, config, **kwargs)
        except Exception as exc:
            await asyncio.to_thread(self._fail, action, exc)
            raise
        return await asyncio.to_thread(self._end, action, call_id, out)

    # Each call is one action: begin, run, record the result, end. The
    # result's turn is written before the action closes: a crash between the
    # two leaves the result visible and the action open for reconcile, rather
    # than a closed action whose result the next instance never sees, which
    # would invite it to make the call again.

    def _begin(self, input: Any) -> tuple[Action, str | None, Any]:
        is_call = isinstance(input, Mapping) and input.get("type") == "tool_call"
        args = dict(input.get("args") or {}) if is_call else (dict(input) if isinstance(input, Mapping) else {"input": input})
        shown = {k: v for k, v in args.items() if k != KEY_ARG}
        action = self.resume.agent.begin_action(self.name, self.action_class, shown)
        if takes_key(self.inner):
            args[KEY_ARG] = action.key
            input = {**input, "args": args} if is_call else args
        return action, (input.get("id") if is_call else None), input

    # Only an ordinary exception is the tool saying it failed. Cancellation or
    # an interrupt says nothing about whether the call took effect, so the
    # action stays open for the next resume to settle.
    def _fail(self, action: Action, exc: Exception) -> None:
        try:
            if isinstance(exc, GraphBubbleUp):
                # interrupt() and friends: LangGraph runs the tool again from
                # the start when the graph resumes, as a new action.
                self.resume.agent.end_action(action.seq, "abandoned",
                                             resolution="stopped by the graph; LangGraph runs it again on resume")
            else:
                self.resume.agent.end_action(action.seq, "failed", error=f"{type(exc).__name__}: {exc}")
        except Exception:
            # The tool's own error is the one the caller needs; an action left
            # open is settled on the next resume.
            pass

    def _end(self, action: Action, call_id: str | None, out: Any) -> Any:
        content = out.content if isinstance(out, ToolMessage) else out
        text = content if isinstance(content, str) else json.dumps(content, default=str)
        if call_id:
            self.resume.record_tool_result(call_id, self.name, text)
        if isinstance(out, ToolMessage) and out.status == "error":
            # A ToolException the tool handled itself comes back as content.
            self.resume.agent.end_action(action.seq, "failed", error=text)
        else:
            self.resume.agent.end_action(action.seq, "done", result=text)
        return out


def wrap(resume: RecallResume, tools: Sequence[BaseTool], classes: Mapping[str, str] | None) -> list[DurableTool]:
    classes = dict(classes or {})
    unknown = sorted(set(classes) - {t.name for t in tools})
    if unknown:
        raise ValueError(f"classes names tools that were not given: {', '.join(unknown)}")
    out, missing = [], []
    for tool in tools:
        if isinstance(tool, DurableTool):
            raise ValueError(f"tool {tool.name!r} is already durable")
        cls = classes.get(tool.name) or (tool.metadata or {}).get("recall_class")
        if cls is None:
            missing.append(tool.name)
            continue
        if cls not in CLASSES:
            raise ValueError(f"tool {tool.name!r} has class {cls!r}; use one of {', '.join(CLASSES)}")
        out.append(DurableTool(name=tool.name, description=tool.description, args_schema=tool.args_schema,
                               return_direct=tool.return_direct, inner=tool, action_class=cls, resume=resume,
                               handle_tool_error=tool.handle_tool_error,
                               handle_validation_error=tool.handle_validation_error))
    if missing:
        # Guessing is what makes a resumed agent send the email twice, so an
        # unclassified tool is refused rather than given a default.
        raise ValueError(
            "every durable tool needs a class: pure (reads only), idempotent (safe to repeat with the same key) "
            f"or external (could happen twice). Unclassified: {', '.join(missing)}")
    return out
