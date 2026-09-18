"""Attach Recall memory to an Agent subclass you already have."""

from __future__ import annotations

from livekit.agents import llm
from livekit.agents.voice import Agent

from .memory import SubjectMemory
from .render import DEFAULT_TEMPLATE, compose_instructions, render_beliefs
from .tools import build_forget_tool, build_remember_tool


class MemoryBinding:
    """Keeps an agent's instructions in step with one caller's beliefs."""

    def __init__(
        self,
        agent: Agent,
        memory: SubjectMemory,
        *,
        base_instructions: str,
        context_template: str = DEFAULT_TEMPLATE,
        who: str = "the caller",
        remember_tool: bool = True,
    ) -> None:
        self.agent = agent
        self.memory = memory
        self.base_instructions = base_instructions
        self.context_template = context_template
        self.who = who
        self.remember_tool = remember_tool

    def render(self) -> str:
        return render_beliefs(
            self.memory.beliefs,
            registry=self.memory.registry,
            template=self.context_template,
            who=self.who,
            remember_tool=self.remember_tool,
        )

    async def refresh(self) -> None:
        """Reload the caller's beliefs and rewrite the agent's instructions."""
        await self.memory.load()
        await self.agent.update_instructions(compose_instructions(self.base_instructions, self.render()))

    def turn_context(self, hits: list) -> str:
        """The block added to a turn when a per-turn search found more facts."""
        return render_beliefs(
            hits,
            registry=self.memory.registry,
            template=self.context_template,
            who=self.who,
            remember_tool=False,
        )


async def attach(
    agent: Agent,
    memory: SubjectMemory,
    *,
    context_template: str = DEFAULT_TEMPLATE,
    who: str = "the caller",
    remember_tool: bool = True,
    forget_tool: bool = False,
) -> MemoryBinding:
    """Give an existing Agent the memory block and the ``remember`` tool.

    Await it before ``session.start`` or at the top of the agent's ``on_enter``.
    The agent's instructions must be a plain string. Per-turn search for
    callers with more beliefs than fit the block needs ``RecallAgent``.
    """
    instructions = agent.instructions
    if not isinstance(instructions, str):
        raise TypeError("attach needs plain string instructions; Instructions objects are not supported")
    binding = MemoryBinding(
        agent,
        memory,
        base_instructions=instructions,
        context_template=context_template,
        who=who,
        remember_tool=remember_tool,
    )
    tools: list[llm.Tool | llm.Toolset] = list(agent.tools)
    if remember_tool:
        tools.append(build_remember_tool(memory, on_change=binding.refresh))
    if forget_tool:
        tools.append(build_forget_tool(memory, on_change=binding.refresh))
    await agent.update_tools(tools)
    await binding.refresh()
    return binding
