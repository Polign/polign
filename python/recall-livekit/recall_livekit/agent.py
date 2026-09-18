"""An Agent that remembers the caller."""

from __future__ import annotations

import asyncio
from typing import Any

from livekit.agents import llm
from livekit.agents.voice import Agent

from .hooks import MemoryBinding
from .memory import SubjectMemory
from .render import DEFAULT_TEMPLATE
from .tools import build_forget_tool, build_remember_tool


class RecallAgent(Agent):
    """A LiveKit Agent whose instructions carry the caller's remembered facts.

    On ``on_enter`` it loads every current belief for the subject and appends
    them to the instructions, before the first reply. The ``remember`` tool
    writes new facts and reloads the block. When a caller has more beliefs
    than ``memory.limit``, each user turn also runs a search and adds the
    matching facts to that turn.

    Subclasses that override ``on_enter`` must ``await super().on_enter()``
    first, before generating a greeting. LiveKit runs ``on_enter`` as a task
    after ``session.start`` returns; ``wait_for_memory`` awaits its first load.
    """

    def __init__(
        self,
        *,
        memory: SubjectMemory,
        instructions: str,
        context_template: str = DEFAULT_TEMPLATE,
        who: str = "the caller",
        remember_tool: bool = True,
        forget_tool: bool = False,
        search_when_overflowed: bool = True,
        tools: list[llm.Tool | llm.Toolset] | None = None,
        **kwargs: Any,
    ) -> None:
        if not isinstance(instructions, str):
            raise TypeError("RecallAgent needs plain string instructions")
        self._binding = MemoryBinding(
            self,
            memory,
            base_instructions=instructions,
            context_template=context_template,
            who=who,
            remember_tool=remember_tool,
        )
        self._search_when_overflowed = search_when_overflowed
        self._memory_loaded = asyncio.Event()
        all_tools: list[llm.Tool | llm.Toolset] = list(tools or [])
        if remember_tool:
            all_tools.append(build_remember_tool(memory, on_change=self.refresh_memory))
        if forget_tool:
            all_tools.append(build_forget_tool(memory, on_change=self.refresh_memory))
        super().__init__(instructions=instructions, tools=all_tools, **kwargs)

    @property
    def memory(self) -> SubjectMemory:
        return self._binding.memory

    @property
    def base_instructions(self) -> str:
        """The instructions without the memory block."""
        return self._binding.base_instructions

    async def refresh_memory(self) -> None:
        """Reload the caller's beliefs and rewrite the instructions."""
        await self._binding.refresh()

    async def on_enter(self) -> None:
        try:
            await self.refresh_memory()
        finally:
            self._memory_loaded.set()

    async def wait_for_memory(self) -> None:
        """Wait until ``on_enter`` has loaded the caller's beliefs. LiveKit runs
        ``on_enter`` as a task after ``session.start`` returns, so tests and
        code that inspects the instructions right after start should await this."""
        await self._memory_loaded.wait()

    async def on_user_turn_completed(
        self, turn_ctx: llm.ChatContext, new_message: llm.ChatMessage
    ) -> None:
        if not self._search_when_overflowed or not self.memory.overflowed:
            return
        text = new_message.text_content
        if not text:
            return
        hits = await self.memory.search(text)
        # Facts already in the instructions add nothing to the turn.
        shown = {(b.predicate, str(b.value)) for b in self.memory.beliefs}
        extra = [b for b in hits if (b.predicate, str(b.value)) not in shown]
        if extra:
            turn_ctx.add_message(role="system", content=self._binding.turn_context(extra))
