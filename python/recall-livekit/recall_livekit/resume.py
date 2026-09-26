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

    Only one process may write for an agent, and a worker that died still
    holds its lease until the lease expires. The caller must not sit in
    silence for that, so ``open`` reads the records without the lease and
    returns the briefing at once; reading cannot conflict with anyone. Turns
    are buffered while a background writer takes the lease, retrying for up to
    ``wait`` seconds, and written in order once it has it. The default
    ``lease_ttl`` of 6 seconds keeps that gap short; the lease is renewed in
    the background while the call runs.

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
        lease_ttl: float = 6.0,
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
        self._held = asyncio.Event()
        self._closing = False

    @property
    def lease_held(self) -> bool:
        """True once the background writer holds the lease, so turns and
        working state updates are being written."""
        return self._held.is_set()

    @property
    def resumed(self) -> bool:
        """True once ``open`` succeeded and until ``release``."""
        return self._agent is not None

    async def open(self) -> ResumeContext | None:
        """Read the agent's records back and return the context, without
        waiting for the lease; the background writer takes it. Returns None
        when the resume failed (the reason is logged)."""
        if self._agent is not None:
            return self.context
        client = self.memory.client
        try:
            # No timeout of our own here: a resume abandoned while it is still
            # running would leave an agent in this worker's session with
            # nothing to release it. The client's timeout bounds the call.
            self._agent = await asyncio.to_thread(
                client.resume, self.agent_id, token_budget=self.token_budget,
                lease_ttl=self.lease_ttl, defer_lease=True,
            )
        except RecallError as exc:
            logger.warning("resume failed for agent %r: %s", self.agent_id, _describe(exc))
            return None
        self.context = self._agent.context
        self._closing = False
        self._held.clear()
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
        if not self._held.is_set():
            # Right after a new worker takes over, the old lease may still be
            # running out. Wait for it rather than drop the update.
            await asyncio.wait_for(self._held.wait(), self.wait)
        return await asyncio.wait_for(
            asyncio.to_thread(lambda: agent.update_working_state(**fields)), self.timeout
        )

    async def release(self) -> None:
        """Write the queued turns, then hand the lease over so the next
        worker need not wait for it to expire. Safe to call more than once."""
        self.detach()
        self._closing = True
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
        deadline = time.monotonic() + self.wait
        while not self._closing:
            try:
                if await asyncio.wait_for(asyncio.to_thread(agent.acquire), self.timeout):
                    self._held.set()
                    break
            except (RecallError, asyncio.TimeoutError) as exc:
                logger.warning("taking the lease failed for agent %r: %s", self.agent_id, _describe(exc))
            if time.monotonic() >= deadline:
                # Someone else is still acting for this call. Writing now
                # would interleave with them, so this worker records nothing.
                logger.warning("agent %r is still held by another worker after %gs; "
                               "this worker's turns are not recorded", self.agent_id, self.wait)
                return
            # Each try is one LIST on the store; once a second is plenty,
            # since the caller is already being answered meanwhile.
            await asyncio.sleep(1.0)
        if not self._held.is_set():
            return
        while True:
            turn = await queue.get()
            if turn is None:
                return
            try:
                await asyncio.wait_for(asyncio.to_thread(agent.record_turn, *turn), self.timeout)
            except (RecallError, asyncio.TimeoutError) as exc:
                logger.warning("recording a turn failed for agent %r: %s", self.agent_id, _describe(exc))
