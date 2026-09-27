"""A stand-in for ``polign_recall.Client(agent=True)`` used by the unit tests.

Keeps one agent's records in memory: its turns, working state, pointers and
outputs, and a lease that a second resume cannot take while it is held.
"""

import threading

from polign_recall import Output, OutputRef, Pointer, RecallError, ResumeContext, Turn, WorkingState


class FakeClient:
    def __init__(self) -> None:
        self.turns: list[Turn] = []
        self.state = WorkingState()
        self.pointers: dict[str, Pointer] = {}
        self.outputs: dict[str, Output] = {"out-1": Output(ref="out-1", content="the whole grep output")}
        self.held_by: object | None = None
        self.resumes: list[dict] = []
        self.closed = False
        self.lock = threading.Lock()

    def resume(self, agent_id, **options):
        with self.lock:
            if self.held_by is not None:
                raise RecallError("recall: agent lease is held by another process", code="lease_held")
            self.resumes.append({"agent_id": agent_id, **options})
            agent = FakeAgent(self, agent_id)
            self.held_by = agent
        recent = "\n".join(f"[{t.role} #{t.seq}] {t.content}" for t in self.turns[-3:])
        briefing = f"Goal: {self.state.goal or '(none)'}\nRecent turns:\n{recent}"
        agent.context = ResumeContext(agent_id=agent_id, fresh=not self.turns, briefing=briefing,
                                      working_state=self.state, turn_seq=len(self.turns))
        return agent

    def close(self) -> None:
        self.closed = True


class FakeAgent:
    def __init__(self, client: FakeClient, agent_id: str) -> None:
        self.client = client
        self.agent_id = agent_id
        self.context: ResumeContext | None = None
        self.released = 0

    def record_turn(self, role, content, name=None, message_id=None, brief=None):
        turn = Turn(seq=len(self.client.turns) + 1, role=role, content=content, name=name or "",
                    message_id=message_id or "", brief=brief or "")
        self.client.turns.append(turn)
        return turn

    def recent_turns(self, limit=None):
        return self.client.turns[-(limit or 20):]

    def update_working_state(self, **fields):
        current = self.client.state
        merged = {**current.__dict__, **{k: tuple(v) if isinstance(v, list) else v for k, v in fields.items()}}
        merged["version"] = current.version + 1
        self.client.state = WorkingState(**merged)
        return self.client.state

    def milestone(self, name, progress=None):
        return self.update_working_state(last_milestone=name, **({"progress": progress} if progress else {}))

    def fetch_output(self, ref):
        try:
            return self.client.outputs[ref]
        except KeyError:
            raise RecallError(f"recall: agent {self.agent_id!r} has no output {ref!r}") from None

    def store_output(self, content, tool=None, summary=None, ref=None):
        ref = ref or f"out-{len(self.client.outputs) + 1}"
        self.client.outputs[ref] = Output(ref=ref, content=content)
        return OutputRef(ref=ref)

    def set_pointer(self, name, type, fields, note=None):
        pointer = Pointer(name=name, type=type, fields=dict(fields), note=note or "")
        self.client.pointers[name] = pointer
        return pointer

    def release(self):
        self.released += 1
        with self.client.lock:
            if self.client.held_by is self:
                self.client.held_by = None
                return True
        return False
