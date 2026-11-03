"""A stand-in for ``polign_recall.Client(agent=True)`` holding many agents,
for the checkpointer and store tests.

Each agent has its outputs (by ref, replaced on store) and a lease. A
deferred resume reads without the lease; a leased resume fails with
"lease_held" while another holder has it; a write without the lease fails
with "lease_not_held", as the server does.
"""

from __future__ import annotations

import threading

from polign_recall import Output, OutputRef, RecallError, ResumeContext, Turn


class FakeAgentsClient:
    def __init__(self) -> None:
        self.outputs: dict[str, dict[str, str]] = {}
        self.turns: dict[str, list[Turn]] = {}
        self.held: dict[str, object] = {}
        self.lock = threading.Lock()
        self.resumes = 0
        self.closed = False
        # The session: at most one resumed agent per id, as the server keeps.
        self.session: dict[str, FakeAgent] = {}

    def resume(self, agent_id, *, defer_lease=False, **options):
        agent = FakeAgent(self, agent_id, leased=not defer_lease)
        with self.lock:
            self.resumes += 1
            if agent_id in self.session:
                raise RecallError(f'agent "{agent_id}" is already resumed in this session; call agent_release first')
            if not defer_lease:
                if agent_id in self.held:
                    raise RecallError("recall: agent lease is held by another process", code="lease_held")
                self.held[agent_id] = agent
            self.session[agent_id] = agent
        agent.context = ResumeContext(agent_id=agent_id, fresh=agent_id not in self.outputs, briefing="",
                                      turn_seq=len(self.turns.get(agent_id, [])), lease_held=not defer_lease)
        return agent

    def close(self) -> None:
        self.closed = True
        with self.lock:
            self.held.clear()


class FakeAgent:
    def __init__(self, client: FakeAgentsClient, agent_id: str, leased: bool) -> None:
        self.client = client
        self.agent_id = agent_id
        self.leased = leased
        self.context: ResumeContext | None = None
        self.closed = False

    def _open(self) -> None:
        if self.closed or self.client.session.get(self.agent_id) is not self:
            raise RecallError(f'agent "{self.agent_id}" is not resumed in this session; call agent_resume first')

    def _writable(self) -> None:
        self._open()
        if not self.leased or self.client.held.get(self.agent_id) is not self:
            raise RecallError("recall: the agent's lease is not held", code="lease_not_held")

    def acquire(self) -> bool:
        self._open()
        with self.client.lock:
            if self.leased:
                return True
            if self.agent_id in self.client.held:
                return False
            self.client.held[self.agent_id] = self
            self.leased = True
            return True

    def store_output(self, content, tool=None, summary=None, ref=None):
        self._writable()
        if len(content.encode()) > 1 << 20:
            raise RecallError("recall: output is too large")
        ref = ref or f"out-{len(self.client.outputs.get(self.agent_id, {})) + 1}"
        self.client.outputs.setdefault(self.agent_id, {})[ref] = content
        return OutputRef(ref=ref)

    def fetch_output(self, ref):
        self._open()
        try:
            return Output(ref=ref, content=self.client.outputs[self.agent_id][ref])
        except KeyError:
            raise RecallError(f"recall: agent {self.agent_id!r} has no output {ref!r}") from None

    def record_turn(self, role, content, name=None, message_id=None, brief=None):
        self._writable()
        turns = self.client.turns.setdefault(self.agent_id, [])
        turn = Turn(seq=len(turns) + 1, role=role, content=content, name=name or "",
                    message_id=message_id or "", brief=brief or "")
        turns.append(turn)
        return turn

    def recent_turns(self, limit=None):
        return self.client.turns.get(self.agent_id, [])[-(limit or 20):]

    def release(self) -> bool:
        with self.client.lock:
            self.closed = True
            if self.client.session.get(self.agent_id) is self:
                del self.client.session[self.agent_id]
            if self.client.held.get(self.agent_id) is self:
                del self.client.held[self.agent_id]
                return True
        return False
