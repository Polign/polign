"""One resumed agent, and the hooks that put it into a LangGraph graph."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import threading
from collections.abc import Mapping, Sequence
from typing import Any

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    ChatMessage,
    HumanMessage,
    RemoveMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.runnables import RunnableLambda
from langgraph.graph.message import REMOVE_ALL_MESSAGES
from polign_recall import Client, Output, Pointer, ResumeContext, ResumedAgent, WorkingState

#: The id of the briefing message ``briefing_node`` adds to the state. The
#: recorder skips it, so the briefing never becomes a turn of its own.
BRIEFING_ID = "recall-briefing"


class RecallResume:
    """Resumes one agent from its Recall records and keeps them current.

    Resuming takes the agent's lease and reads back the context to start
    from: its working state, pointers to its work, relevant memories, and its
    most recent turns, within ``token_budget``. ``briefing`` is that context
    as one text for the model. The first use opens it; call ``open`` (or
    ``aopen``) yourself to resume at a point you choose.

    Old messages are not replayed into the state: the briefing already
    carries the recent turns as text, and a replayed tool result without its
    matching call would be refused by the model API. Your checkpointer keeps
    working as usual. On a new thread id the resumed run starts from the
    briefing alone; on the old thread id the checkpointer restores the old
    messages too, and nothing is recorded twice: each turn keeps its message
    id, so the first recording after a resume skips every restored message up
    to the last one the records already have.

    For ``create_react_agent``, pass ``pre_model_hook`` and
    ``post_model_hook``. For your own StateGraph, call ``with_briefing`` on the
    messages you send the model (or add ``briefing_node``), and add
    ``record_node`` where new messages land.

    ``client`` is a ``polign_recall.Client`` opened with ``agent=True``. When
    it is left out, one is opened with ``local_dir`` and ``env`` and closed on
    release. Release on a clean shutdown (``release``, ``arelease``, or a
    ``with`` block) so the next process can resume at once instead of waiting
    for the lease to expire.
    """

    def __init__(
        self,
        agent_id: str,
        *,
        client: Client | None = None,
        token_budget: int | None = None,
        lease_ttl: float | None = None,
        holder: str | None = None,
        output_threshold: int | None = None,
        local_dir: str | os.PathLike[str] | None = None,
        env: Mapping[str, str] | None = None,
    ) -> None:
        if client is not None and (local_dir is not None or env is not None):
            raise ValueError("local_dir and env open a client; do not pass them with client")
        self.agent_id = agent_id
        self._client = client
        self._owns_client = client is None
        self._client_options: dict[str, Any] = {"local_dir": local_dir, "env": env}
        self._resume_options: dict[str, Any] = {
            "token_budget": token_budget,
            "lease_ttl": lease_ttl,
            "holder": holder,
            "output_threshold": output_threshold,
        }
        self._agent: ResumedAgent | None = None
        self._released = False
        self._open_lock = threading.Lock()
        # Serializes recording, so the pre and post hooks of parallel branches
        # cannot record the same message twice.
        self._record_lock = threading.Lock()
        self._recorded: set[str] = set()
        self._seeded = False

    # Lifecycle.

    def open(self) -> RecallResume:
        """Resume the agent, once. Raises ``polign_recall.RecallError`` with
        code "lease_held" while another process holds it."""
        with self._open_lock:
            if self._released:
                raise RuntimeError("this resume was released; create a new one to resume again")
            if self._agent is not None:
                return self
            if self._client is None:
                self._client = Client(agent=True, **self._client_options)
            try:
                self._agent = self._client.resume(self.agent_id, **self._resume_options)
            except BaseException:
                if self._owns_client:
                    self._client.close()
                    self._client = None
                raise
            return self

    async def aopen(self) -> RecallResume:
        return await asyncio.to_thread(self.open)

    def release(self) -> None:
        """Hand the lease over and, if this object opened its own client,
        close it. Safe to call more than once."""
        with self._open_lock:
            if self._released:
                return
            self._released = True
            agent, client = self._agent, self._client
            self._agent = None
        try:
            if agent is not None:
                agent.release()
        finally:
            if self._owns_client and client is not None:
                client.close()

    async def arelease(self) -> None:
        await asyncio.to_thread(self.release)

    def __enter__(self) -> RecallResume:
        return self.open()

    def __exit__(self, *exc: object) -> None:
        self.release()

    async def __aenter__(self) -> RecallResume:
        return await self.aopen()

    async def __aexit__(self, *exc: object) -> None:
        await self.arelease()

    @property
    def agent(self) -> ResumedAgent:
        """The resumed agent, for the calls this class does not wrap. Opens it
        on first use."""
        if self._agent is None:
            self.open()
        assert self._agent is not None
        return self._agent

    @property
    def context(self) -> ResumeContext:
        """What the resume returned."""
        return self.agent.context

    @property
    def briefing(self) -> str:
        return self.context.briefing

    # The briefing.

    def briefing_message(self) -> SystemMessage:
        return SystemMessage(content=self.briefing, id=BRIEFING_ID)

    def with_briefing(self, messages: Sequence[BaseMessage]) -> list[BaseMessage]:
        """``messages`` with the briefing in front, for the model's input. It
        does not change the graph state. A briefing already there is not
        added twice."""
        messages = list(messages)
        if any(m.id == BRIEFING_ID for m in messages):
            return messages
        return [self.briefing_message(), *messages]

    def _pre(self, state: Any) -> dict[str, Any]:
        messages = _messages(state)
        self.record(messages)
        return {"llm_input_messages": self.with_briefing(messages)}

    async def _apre(self, state: Any) -> dict[str, Any]:
        return await asyncio.to_thread(self._pre, state)

    def _post(self, state: Any) -> dict[str, Any]:
        self.record(_messages(state))
        return {}

    async def _apost(self, state: Any) -> dict[str, Any]:
        return await asyncio.to_thread(self._post, state)

    def _briefing_update(self, state: Any) -> dict[str, Any]:
        messages = _messages(state)
        if any(m.id == BRIEFING_ID for m in messages):
            return {}
        # add_messages appends, so putting the briefing first means rewriting
        # the list, the way LangGraph documents for a pre-model hook.
        return {"messages": [RemoveMessage(id=REMOVE_ALL_MESSAGES), self.briefing_message(), *messages]}

    async def _abriefing_update(self, state: Any) -> dict[str, Any]:
        return await asyncio.to_thread(self._briefing_update, state)

    @property
    def pre_model_hook(self) -> RunnableLambda:
        """For ``create_react_agent(pre_model_hook=...)``. Records the messages
        that arrived since the last model call and hands the model the
        briefing followed by the state's messages, as ``llm_input_messages``,
        so the state itself is unchanged. To combine it with a hook of your
        own (trimming, say), call ``record`` and ``with_briefing`` from it."""
        return RunnableLambda(self._pre, afunc=self._apre, name="recall_pre_model_hook")

    @property
    def post_model_hook(self) -> RunnableLambda:
        """For ``create_react_agent(post_model_hook=...)``. Records the model's
        reply as soon as it arrives, so a crash during the tool calls that
        follow does not lose it."""
        return RunnableLambda(self._post, afunc=self._apost, name="recall_post_model_hook")

    @property
    def record_node(self) -> RunnableLambda:
        """A node for your own StateGraph that records every message not
        recorded yet and changes nothing in the state."""
        return RunnableLambda(self._post, afunc=self._apost, name="recall_record")

    @property
    def briefing_node(self) -> RunnableLambda:
        """A node for your own StateGraph that adds the briefing to the
        ``messages`` state as a SystemMessage, once per thread. Put it first.
        Prefer ``with_briefing`` in your model node when you can: it keeps the
        briefing out of the state."""
        return RunnableLambda(self._briefing_update, afunc=self._abriefing_update, name="recall_briefing")

    # Recording.

    def record(self, messages: Sequence[BaseMessage]) -> int:
        """Record each message not recorded yet, in order, as a turn. Returns
        how many were recorded. Messages are told apart by id; the briefing
        and messages without text are skipped."""
        count = 0
        with self._record_lock:
            if not self._seeded:
                self._seed(messages)
                self._seeded = True
            for message in messages:
                key = _key(message)
                if key in self._recorded or message.id == BRIEFING_ID:
                    continue
                turn = _turn(message)
                if turn is not None:
                    role, content, name = turn
                    brief = _brief(message)
                    self.agent.record_turn(role, content, name, message_id=message.id or None,
                                           brief=brief if brief and brief != content else None)
                    count += 1
                self._recorded.add(key)
        return count

    def _seed(self, messages: Sequence[BaseMessage]) -> None:
        """Mark messages a checkpointer restored as already recorded.

        The previous process recorded in order, so every message up to the
        last one whose id the records hold was recorded, whether or not its
        own id made it into the recent turns. Anything after that is new: the
        caller's fresh input, or a message the previous process got but died
        before recording, and both should be recorded now."""
        if self.agent.context.turn_seq == 0:
            return
        known = {t.message_id for t in self.agent.recent_turns(200) if t.message_id}
        last = -1
        for i, message in enumerate(messages):
            if message.id and message.id in known:
                last = i
        for message in messages[: last + 1]:
            self._recorded.add(_key(message))

    async def arecord(self, messages: Sequence[BaseMessage]) -> int:
        return await asyncio.to_thread(self.record, messages)

    # What the model tools call.

    def update_working_state(self, **fields: Any) -> WorkingState:
        return self.agent.update_working_state(**fields)

    def milestone(self, name: str, progress: str | None = None) -> WorkingState:
        return self.agent.milestone(name, progress)

    def fetch_output(self, ref: str) -> Output:
        return self.agent.fetch_output(ref)

    def set_pointer(self, name: str, type: str, fields: Mapping[str, str], note: str | None = None) -> Pointer:
        return self.agent.set_pointer(name, type, fields, note)


def _messages(state: Any) -> list[BaseMessage]:
    messages = state.get("messages") if isinstance(state, Mapping) else getattr(state, "messages", None)
    if messages is None:
        raise ValueError("the graph state has no messages")
    return list(messages)


#: Arguments longer than this are shortened in the briefing. The record keeps
#: them whole; a long argument (a file written, a message sent) usually lives
#: on in what the call produced, so the next instance rarely needs it again.
BRIEF_ARG_CHARS = 200


def _call_text(call: Mapping[str, Any], limit: int | None) -> str:
    """A tool call as the model would write it: name(arg=value, ...), with no
    call id. With a limit, longer argument values are replaced by their
    length."""
    parts = []
    for key, value in (call.get("args") or {}).items():
        shown = json.dumps(value, ensure_ascii=False, default=str)
        if limit is not None and len(shown) > limit:
            size = len(value) if isinstance(value, str) else len(shown)
            shown = f"<{size} chars>"
        parts.append(f"{key}={shown}")
    return f"{call['name']}({', '.join(parts)})"


def _brief(message: BaseMessage) -> str | None:
    """The shorter form of a model reply with tool calls, for the briefing."""
    if not isinstance(message, AIMessage) or not message.tool_calls:
        return None
    text = _text(message.content)
    calls = "\n".join(_call_text(c, BRIEF_ARG_CHARS) for c in message.tool_calls)
    return f"{text}\n{calls}" if text else calls


def _key(message: BaseMessage) -> str:
    if message.id:
        return message.id
    # LangGraph's add_messages gives every message an id, so this is only for
    # lists built by hand.
    body = json.dumps([message.type, message.content, getattr(message, "tool_call_id", None)],
                      default=str, sort_keys=True)
    return "sha256:" + hashlib.sha256(body.encode()).hexdigest()


def _text(content: Any) -> str:
    """The readable text of a message: a string, or a list of content blocks."""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts = []
    for block in content:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict):
            if isinstance(block.get("text"), str):
                parts.append(block["text"])
            elif block.get("type") not in (None, "tool_use", "tool_call"):
                parts.append(f"[{block['type']}]")
    return "\n".join(p for p in parts if p)


def _turn(message: BaseMessage) -> tuple[str, str, str | None] | None:
    """The (role, content, name) turn one message is recorded as."""
    text = _text(message.content)
    if isinstance(message, HumanMessage):
        return ("user", text, None) if text else None
    if isinstance(message, AIMessage):
        calls = "\n".join(_call_text(c, None) for c in message.tool_calls)
        if calls:
            text = f"{text}\n{calls}" if text else calls
        return ("assistant", text, None) if text else None
    if isinstance(message, ToolMessage):
        return "tool", text or json.dumps(message.content, default=str), message.name
    if isinstance(message, SystemMessage):
        return ("system", text, None) if text else None
    if isinstance(message, ChatMessage) and message.role in ("system", "user", "assistant", "tool"):
        return (message.role, text, None) if text else None
    return ("assistant", text, None) if text else None
