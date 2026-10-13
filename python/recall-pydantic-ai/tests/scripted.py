"""A scripted model: replays tool calls and records what each request saw."""

from dataclasses import dataclass, field
from typing import Any

from pydantic_ai.messages import ModelMessage, ModelResponse, RetryPromptPart, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, FunctionModel


@dataclass
class Seen:
    instructions: str
    tools: list[str]
    parts: list[Any]


@dataclass
class Scripted:
    """Each run's first request makes the given tool calls; the next answers
    with the text of the last tool result."""

    calls: list[tuple[str, dict]] = field(default_factory=list)
    seen: list[Seen] = field(default_factory=list)

    def model(self) -> FunctionModel:
        return FunctionModel(self._respond)

    def _respond(self, messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        last = messages[-1].parts
        self.seen.append(Seen(info.instructions or "", [t.name for t in info.function_tools], list(last)))
        results = [p for p in last if isinstance(p, (ToolReturnPart, RetryPromptPart))]
        if results:
            return ModelResponse(parts=[TextPart(" | ".join(str(p.content) for p in results))])
        if self.calls:
            calls, self.calls = self.calls, []
            return ModelResponse(parts=[ToolCallPart(name, args) for name, args in calls])
        return ModelResponse(parts=[TextPart("done")])
