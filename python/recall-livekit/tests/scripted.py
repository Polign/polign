"""A scripted stand-in for the model, shared by the unit and integration tests."""

import json
from collections.abc import Sequence

from livekit.agents import llm, utils
from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS, NOT_GIVEN


class ScriptedLLM(llm.LLM):
    """Replays a script: a string is a spoken reply, a (tool, args) pair is a call.

    Every ``chat`` call records the chat context and tools it was given, so a
    test can check what the model would have seen.
    """

    def __init__(self, steps: Sequence[str | tuple[str, dict]]) -> None:
        super().__init__()
        self.steps = list(steps)
        self.calls: list[tuple[llm.ChatContext, list[llm.Tool]]] = []

    @property
    def model(self) -> str:
        return "scripted"

    @property
    def provider(self) -> str:
        return "test"

    def chat(
        self,
        *,
        chat_ctx: llm.ChatContext,
        tools: list[llm.Tool] | None = None,
        conn_options=DEFAULT_API_CONNECT_OPTIONS,
        parallel_tool_calls=NOT_GIVEN,
        tool_choice=NOT_GIVEN,
        extra_kwargs=NOT_GIVEN,
    ) -> llm.LLMStream:
        self.calls.append((chat_ctx.copy(), list(tools or [])))
        step = self.steps.pop(0) if self.steps else "Okay."
        return ScriptedStream(self, chat_ctx=chat_ctx, tools=tools or [], conn_options=conn_options, step=step)


class ScriptedStream(llm.LLMStream):
    def __init__(self, llm_v, *, chat_ctx, tools, conn_options, step) -> None:
        super().__init__(llm_v, chat_ctx=chat_ctx, tools=tools, conn_options=conn_options)
        self._step = step

    async def _run(self) -> None:
        if isinstance(self._step, str):
            delta = llm.ChoiceDelta(role="assistant", content=self._step)
        else:
            name, args = self._step
            delta = llm.ChoiceDelta(
                role="assistant",
                tool_calls=[
                    llm.FunctionToolCall(
                        name=name, arguments=json.dumps(args), call_id=utils.shortuuid("call_")
                    )
                ],
            )
        self._event_ch.send_nowait(llm.ChatChunk(id=utils.shortuuid("chunk_"), delta=delta))


def seen_text(chat_ctx: llm.ChatContext) -> str:
    """Every piece of text in a chat context, for substring checks."""
    parts = []
    for item in chat_ctx.items:
        if item.type == "message":
            parts.append(item.text_content or "")
    return "\n".join(parts)
