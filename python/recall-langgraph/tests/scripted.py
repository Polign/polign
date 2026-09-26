"""A scripted stand-in for the chat model, shared by the unit and integration tests."""

from collections.abc import Sequence
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import PrivateAttr


class ScriptedChatModel(BaseChatModel):
    """Replays a script: a string is a reply, a (tool, args) pair is a tool call.

    Every call records the messages it was given, so a test can check what the
    model would have seen. ``bind_tools`` returns the model itself.
    """

    steps: list[Any]
    _inputs: list[list[BaseMessage]] = PrivateAttr(default_factory=list)

    def __init__(self, steps: Sequence[str | tuple[str, dict]], **kwargs: Any) -> None:
        super().__init__(steps=list(steps), **kwargs)

    @property
    def inputs(self) -> list[list[BaseMessage]]:
        return self._inputs

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools: Any, **kwargs: Any) -> "ScriptedChatModel":
        return self

    def _generate(self, messages: list[BaseMessage], stop=None, run_manager=None, **kwargs: Any) -> ChatResult:
        self._inputs.append(list(messages))
        step = self.steps.pop(0) if self.steps else "Okay."
        n = len(self._inputs)
        if isinstance(step, str):
            message = AIMessage(content=step, id=f"ai-{n}")
        else:
            name, args = step
            message = AIMessage(content="", id=f"ai-{n}",
                                tool_calls=[{"name": name, "args": args, "id": f"call-{n}", "type": "tool_call"}])
        return ChatResult(generations=[ChatGeneration(message=message)])


def seen_text(messages: Sequence[BaseMessage]) -> str:
    """Every piece of text in a model input, for substring checks."""
    return "\n".join(m.content if isinstance(m.content, str) else str(m.content) for m in messages)
