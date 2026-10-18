"""A stand-in for ``polign_recall.Client(agent=True)`` used by the unit tests.

Keeps one agent's records in memory: its turns, working state, pointers,
outputs and actions, and a lease that a second resume cannot take while it is
held. Resuming settles open actions the way the server does: interrupted pure
actions are abandoned and external ones marked unknown.
"""

import dataclasses
import hashlib
import json
import threading

from polign_recall import (Action, Output, OutputRef, Pointer, RecallError, ResumeContext, ResumedAgent, Turn,
                           WorkingState)


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
        self.actions: dict[int, Action] = {}
        # Every call the fake tools received: (tool, args).
        self.calls: list[tuple[str, dict]] = []

    def resume(self, agent_id, **options):
        with self.lock:
            if self.held_by is not None:
                raise RecallError("recall: agent lease is held by another process", code="lease_held")
            self.resumes.append({"agent_id": agent_id, **options})
            agent = FakeAgent(self, agent_id)
            self.held_by = agent
        for seq, action in list(self.actions.items()):
            if action.status == "started" and action.class_ == "pure":
                self.actions[seq] = dataclasses.replace(action, status="abandoned")
            elif action.status == "started" and action.class_ == "external":
                self.actions[seq] = dataclasses.replace(action, status="unknown")
        agent.context = self.context(agent_id)
        return agent

    def context(self, agent_id):
        recent = "\n".join(f"[{t.role} #{t.seq}] {t.content}" for t in self.turns[-3:])
        pending = tuple(a for a in self.actions.values() if a.open)
        unfinished = "".join(f"Unfinished: {a.tool}({a.args}) [{a.class_}]\n" for a in pending)
        briefing = f"{unfinished}Goal: {self.state.goal or '(none)'}\nRecent turns:\n{recent}"
        return ResumeContext(agent_id=agent_id, fresh=not self.turns and not self.actions, briefing=briefing,
                             working_state=self.state, turn_seq=len(self.turns), pending_actions=pending)

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

    def begin_action(self, tool, class_, args=None):
        if args is not None and not isinstance(args, str):
            args = json.dumps(args, sort_keys=True, default=str)
        seq = len(self.client.actions) + 1
        key = hashlib.sha256(f"{self.agent_id}:{seq}".encode()).hexdigest()[:32]
        action = Action(seq=seq, tool=tool, class_=class_, key=key, status="started", args=args or "")
        self.client.actions[seq] = action
        return action

    def end_action(self, seq, status, result=None, error=None, resolution=None):
        action = self.client.actions.get(seq)
        if action is None or not action.open:
            raise RecallError(f"recall: action {seq} is not open")
        if result is not None and not isinstance(result, str):
            result = json.dumps(result, sort_keys=True, default=str)
        action = dataclasses.replace(action, status=status, result=result or "", error=error or "",
                                     resolution=resolution or action.resolution)
        self.client.actions[seq] = action
        return action

    def pending_actions(self):
        return [a for a in self.client.actions.values() if a.open]

    def refresh(self):
        self.context = self.client.context(self.agent_id)
        return self.context

    # The real client's reconcile, run over this fake's records.
    reconcile = ResumedAgent.reconcile

    def release(self):
        self.released += 1
        with self.client.lock:
            if self.client.held_by is self:
                self.client.held_by = None
                return True
        return False
