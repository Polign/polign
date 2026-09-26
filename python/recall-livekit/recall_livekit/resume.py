"""Resume a voice agent from its records after its worker dies mid-call."""

from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Any

from livekit.agents import llm
from livekit.agents.voice import AgentSession
from livekit.agents.voice.events import ConversationItemAddedEvent, FunctionToolsExecutedEvent
from polign_recall import RecallError, ResumeContext, ResumedAgent, WorkingState

from .memory import RecallMemory, _describe

logger = logging.getLogger("recall_livekit")

_AGENT_ID = re.compile(r"^[A-Za-z0-9._-]{1,128}$")

DEFAULT_RESUME_TEMPLATE = "<recall_resume>\n{context}\n</recall_resume>"


class AgentResume:
    """One call's agent records: resumed on entry, written as the call goes.

    ``agent_id`` names the call, not the caller; the room name is a good
    choice. When the worker running the call dies, LiveKit gives the room to a
    new worker, and its agent resumes with the same id: it gets a briefing
    built from the working state and the most recent turns, instead of
    starting the conversation over.

    Only one process may hold an agent. A worker that died still holds it
    until its lease expires, so ``open`` retries for up to ``wait`` seconds.
    The default ``lease_ttl`` of 15 seconds keeps that wait short; the lease is
    renewed in the background while the call runs.

    Like the memory reads, resuming and recording fail open: if Recall is slow
    or down, the call goes on without a briefing and a warning is logged.
    Updates to the working state raise, so the tool can tell the model.
    """

    def __init__(
        self,
        memory: RecallMemory,
        agent_id: str,
        *,
        token_budget: int | None = None,
        lease_ttl: float = 15.0,
        wait: float | None = None,
        timeout: float = 5.0,
    ) -> None:
        # timeout bounds each turn write, working state update and release.
        if not _AGENT_ID.match(agent_id):
            raise ValueError(
                f"agent id must be 1-128 letters, digits, '.', '_' or '-', got {agent_id!r}"
            )
        if not 5 <= lease_ttl <= 3600:
            raise ValueError("lease_ttl must be between 5 seconds and 1 hour")
        if not memory.client.agent_enabled:
            raise ValueError("resuming needs RecallMemory.open(..., agent=True)")
        self.memory = memory
        self.agent_id = agent_id
        self.token_budget = token_budget
        self.lease_ttl = lease_ttl
        self.wait = lease_ttl + 5.0 if wait is None else wait
        self.timeout = timeout
        self.context: ResumeContext | None = None
        self._agent: ResumedAgent | None = None
        self._queue: asyncio.Queue[tuple[str, str, str | None] | None] | None = None
        self._writer: asyncio.Task[None] | None = None
        self._session: AgentSession | None = None

    @property
    def resumed(self) -> bool:
        """True once ``open`` succeeded and until ``release``."""
        return self._agent is not None

    async def open(self) -> ResumeContext | None:
        """Take the agent's lease and read its records back. Returns the
        context, or None when the resume failed (the reason is logged)."""
        if self._agent is not None:
            return self.context
        client = self.memory.client
        deadline = time.monotonic() + self.wait
        while True:
            try:
                # No timeout of our own here: a resume abandoned while it is
                # still running would leave the lease held by this worker's
                # session with nothing to release it. The client's timeout
                # bounds the call instead.
                self._agent = await asyncio.to_thread(
                    client.resume, self.agent_id,
                    token_budget=self.token_budget, lease_ttl=self.lease_ttl,
                )
                break
            except RecallError as exc:
                if exc.code == "lease_held" and time.monotonic() < deadline:
                    # The worker that had this call may have died; its lease
                    # runs out within lease_ttl.
                    await asyncio.sleep(min(1.0, max(0.0, deadline - time.monotonic())))
                    continue
                logger.warning("resume failed for agent %r: %s", self.agent_id, _describe(exc))
                return None
        self.context = self._agent.context
        self._queue = asyncio.Queue()
        self._writer = asyncio.create_task(self._write_turns(), name="recall_resume_writer")
        return self.context

    def render(self, template: str = DEFAULT_RESUME_TEMPLATE, *, working_state_tool: bool = True) -> str:
        """The block added to the instructions. Empty before a resume."""
        if self.context is None:
            return ""
        if "{context}" not in template:
            raise ValueError("resume template must contain a literal {context} placeholder")
        lines = []
        if not self.context.fresh:
            lines.append(
                "This call was cut off and is continuing on a new connection. Pick up where "
                "it left off; do not greet the caller again or repeat what was already said. "
                "What you had noted and the last turns:"
            )
            lines.append(self.context.briefing)
        if working_state_tool:
            lines.append(
                "Keep your working state current with the update_working_state tool: what the "
                "caller needs, what is done, and what you are doing now. If the call drops, "
                "that note is what you continue from."
            )
        return template.replace("{context}", "\n\n".join(lines))

    def attach(self, session: AgentSession) -> None:
        """Record every conversation item and tool call of ``session`` as a turn."""
        if self._session is session:
            return
        self.detach()
        self._session = session
        session.on("conversation_item_added", self._on_item)
        session.on("function_tools_executed", self._on_tools)

    def detach(self) -> None:
        if self._session is not None:
            self._session.off("conversation_item_added", self._on_item)
            self._session.off("function_tools_executed", self._on_tools)
            self._session = None

    def record(self, role: str, content: str, name: str | None = None) -> None:
        """Queue one turn. Turns are written in order, in the background."""
        if self._queue is not None and content:
            self._queue.put_nowait((role, content, name))

    async def update_working_state(self, **fields: Any) -> WorkingState:
        if self._agent is None:
            raise RecallError("the agent is not resumed", code="not_resumed")
        agent = self._agent
        return await asyncio.wait_for(
            asyncio.to_thread(lambda: agent.update_working_state(**fields)), self.timeout
        )

    async def release(self) -> None:
        """Write the queued turns, then hand the lease over so the next
        worker need not wait for it to expire. Safe to call more than once."""
        self.detach()
        agent, writer, queue = self._agent, self._writer, self._queue
        self._agent = self._writer = self._queue = None
        if queue is not None and writer is not None:
            queue.put_nowait(None)
            try:
                await asyncio.wait_for(writer, self.timeout)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                writer.cancel()
        if agent is not None:
            try:
                await asyncio.wait_for(asyncio.to_thread(agent.release), self.timeout)
            except (RecallError, asyncio.TimeoutError) as exc:
                logger.warning("release failed for agent %r: %s", self.agent_id, _describe(exc))

    def _on_item(self, event: ConversationItemAddedEvent) -> None:
        item = event.item
        if not isinstance(item, llm.ChatMessage):
            return
        role = {"developer": "system"}.get(item.role, item.role)
        self.record(role, item.text_content or "")

    def _on_tools(self, event: FunctionToolsExecutedEvent) -> None:
        for call, output in zip(event.function_calls, event.function_call_outputs):
            self.record("assistant", call.arguments, call.name)
            self.record("tool", output.output, output.name or call.name)

    async def _write_turns(self) -> None:
        assert self._queue is not None and self._agent is not None
        queue, agent = self._queue, self._agent
        while True:
            turn = await queue.get()
            if turn is None:
                return
            try:
                await asyncio.wait_for(asyncio.to_thread(agent.record_turn, *turn), self.timeout)
            except (RecallError, asyncio.TimeoutError) as exc:
                logger.warning("recording a turn failed for agent %r: %s", self.agent_id, _describe(exc))
