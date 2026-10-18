"""The kill test: does an agent killed in the middle of a tool call, then
resumed from its Recall records, ever repeat a side effect, or lose one?

No model and no API key. The agent is a scripted, cautious policy: it calls
lookup_order, save_invoice and send_email in turn, skips a tool whose result
it can see in its context, and does not repeat a call its briefing says may
already have happened. What it can see is exactly what Recall hands it, so
the test measures what the harness tells a resumed agent, not how clever a
model is.

Each trial runs two processes against one polign-server:

  1  runs the task and is SIGKILLed, with its Recall subprocess, at the kill
     point: "before" (the call is recorded but has not taken effect),
     "after" (it took effect but the tool has not returned) or "returned"
     (the tool returned; the next model call never starts);
  2  resumes the agent and finishes.

The "world" is files the tools append to, outside both processes, standing
in for a mail server and an invoice store. Three modes are compared:

  plain              recall-langgraph resume, ordinary tools, no keys
  durable            RecallResume.durable, no resolver
  durable+resolver   durable, with a resolver that checks the mail log

    POLIGN_SERVER=/path/to/polign-server RECALL_BIN=/path/to/recall \\
        python kill_actions.py --runs 20 --parallel 8

Results go to results/kill-actions-<time>/: runs.jsonl, one row per trial,
and summary.json.
"""


import argparse
import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
MODES = ("plain", "durable", "durable+resolver")
TOOLS = ("lookup_order", "save_invoice", "send_email")
POINTS = ("before", "after", "returned")
# What each tool returns; the policy treats a marker in its context as "done".
MARKER = {"lookup_order": "LOOKUP-OK", "save_invoice": "SAVE-OK", "send_email": "SEND-OK"}
ARGS = {"lookup_order": {"order": "A-17"}, "save_invoice": {"invoice": "inv-17", "total": 410},
        "send_email": {"to": "maria@example.com", "subject": "Your car is ready"}}
CLASSES = {"lookup_order": "pure", "save_invoice": "idempotent", "send_email": "external"}


# --- one agent process -------------------------------------------------------

def worker(args: argparse.Namespace) -> None:
    import warnings
    from typing import Annotated

    from langchain_core.language_models import BaseChatModel
    from langchain_core.messages import AIMessage, ToolMessage
    from langchain_core.outputs import ChatGeneration, ChatResult
    from langchain_core.tools import InjectedToolArg, tool
    from polign_recall import ActionEnd, RecallError
    from recall_langgraph import RecallResume, recall_tools

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        from langgraph.prebuilt import create_react_agent

    world = Path(args.world)
    kill_tool, kill_point = (args.kill.split(":") if args.kill else (None, None))

    def die() -> None:
        # The process group is this worker and its Recall subprocess: both go
        # at once, nothing is flushed or released, as when a pod is killed.
        os.killpg(os.getpgrp(), signal.SIGKILL)
        # The signal lands asynchronously, and tools run on a worker thread:
        # without this the caller could take one more step before it dies.
        while True:
            time.sleep(60)

    def effect(name: str, row: dict) -> None:
        with open(world / f"{name}.jsonl", "a") as f:
            f.write(json.dumps(row) + "\n")
            f.flush()
            os.fsync(f.fileno())

    def maybe_die(name: str, point: str) -> None:
        if name == kill_tool and point == kill_point:
            die()

    @tool
    def lookup_order(order: str) -> str:
        """Look up an order."""
        maybe_die("lookup_order", "before")
        effect("lookups", {"order": order})
        maybe_die("lookup_order", "after")
        return f"{MARKER['lookup_order']} order {order}: brake pads, ready"

    @tool
    def save_invoice(invoice: str, total: int, idempotency_key: Annotated[str, InjectedToolArg] = "") -> str:
        """Save an invoice."""
        maybe_die("save_invoice", "before")
        effect("invoices", {"invoice": invoice, "key": idempotency_key or None})
        maybe_die("save_invoice", "after")
        return f"{MARKER['save_invoice']} saved {invoice}"

    @tool
    def send_email(to: str, subject: str) -> str:
        """Send an email."""
        maybe_die("send_email", "before")
        effect("sent", {"to": to, "subject": subject})
        maybe_die("send_email", "after")
        return f"{MARKER['send_email']} sent"

    def mail_log(action) -> ActionEnd | None:
        """The resolver: the mail server's log is the outside system."""
        sent = world / "sent.jsonl"
        rows = [json.loads(line) for line in sent.read_text().splitlines()] if sent.exists() else []
        wanted = json.loads(action.args or "{}").get("subject")
        if any(r["subject"] == wanted for r in rows):
            return ActionEnd("done", result=MARKER["send_email"] + " sent", resolution="found in the mail log")
        return ActionEnd("failed", resolution="not in the mail log")

    class Policy(BaseChatModel):
        """Calls the next tool whose result it cannot see, unless its briefing
        says that call may already have happened."""

        @property
        def _llm_type(self) -> str:
            return "policy"

        def bind_tools(self, tools, **kwargs):
            return self

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            if kill_point == "returned" and kill_tool and messages and isinstance(messages[-1], ToolMessage) \
                    and messages[-1].name == kill_tool:
                die()
            text = "\n".join(m.content if isinstance(m.content, str) else str(m.content) for m in messages)
            unfinished = ""
            if "## Unfinished actions" in text:
                unfinished = text.split("## Unfinished actions", 1)[1].split("\n## ", 1)[0]
            for name in TOOLS:
                if MARKER[name] in text:
                    continue
                if f"{name}(" in unfinished and "Check before repeating" in unfinished:
                    # A cautious agent does not repeat a call that may have
                    # happened; it reports it for someone to check.
                    continue
                call = {"name": name, "args": ARGS[name], "id": "call-" + uuid.uuid4().hex[:12], "type": "tool_call"}
                message = AIMessage(content="", id="ai-" + uuid.uuid4().hex, tool_calls=[call])
                return ChatResult(generations=[ChatGeneration(message=message)])
            message = AIMessage(content="FINISHED", id="ai-" + uuid.uuid4().hex)
            return ChatResult(generations=[ChatGeneration(message=message)])

    resolver = mail_log if args.mode == "durable+resolver" else None
    resume = RecallResume(args.agent, lease_ttl=5, resolver=resolver)
    deadline = time.monotonic() + 30
    while True:
        try:
            resume.open()
            break
        except RecallError as exc:
            if exc.code != "lease_held" or time.monotonic() > deadline:
                raise
            time.sleep(0.25)

    plain = [lookup_order, save_invoice, send_email]
    tools = plain if args.mode == "plain" else resume.durable(plain, CLASSES)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        graph = create_react_agent(Policy(), [*tools, *recall_tools(resume)],
                                   pre_model_hook=resume.pre_model_hook, post_model_hook=resume.post_model_hook)
    result = graph.invoke({"messages": [("user", "Close out order A-17 and tell Maria." if resume.context.fresh
                                         else "Carry on from where you left off.")]})
    pending = [a.tool for a in resume.agent.pending_actions()] if args.mode != "plain" else []
    (world / f"phase{args.phase}.json").write_text(json.dumps({
        "final": result["messages"][-1].content, "pending": pending}))
    resume.release()


# --- the orchestrator --------------------------------------------------------

def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_server(binary: str, data: Path) -> tuple[subprocess.Popen, str]:
    http = free_port()
    proc = subprocess.Popen([binary, "-store", f"fs:{data}", "-telemetry=false", "-cold-first=false",
                             "-http", f"127.0.0.1:{http}", "-grpc", f"127.0.0.1:{free_port()}"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    url = f"http://127.0.0.1:{http}"
    deadline = time.time() + 20
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url + "/healthz", timeout=1):
                return proc, url
        except Exception:
            time.sleep(0.1)
    proc.kill()
    raise RuntimeError("polign-server did not start")


def count(world: Path, name: str) -> list[dict]:
    path = world / f"{name}.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def trial(mode: str, tool: str, point: str, n: int, root: Path, env: dict) -> dict:
    world = root / f"{mode}-{tool}-{point}-{n}"
    world.mkdir(parents=True)
    agent = f"kill-{uuid.uuid4().hex[:10]}"
    base = [sys.executable, __file__, "--worker", "--mode", mode, "--agent", agent, "--world", str(world)]
    p1 = subprocess.run(base + ["--phase", "1", "--kill", f"{tool}:{point}"], env=env, timeout=120,
                        start_new_session=True, capture_output=True, text=True)
    killed = p1.returncode == -signal.SIGKILL
    p2 = subprocess.run(base + ["--phase", "2"], env=env, timeout=120, start_new_session=True,
                        capture_output=True, text=True)
    phase2 = json.loads((world / "phase2.json").read_text()) if (world / "phase2.json").exists() else {}
    sent, invoices = count(world, "sent"), count(world, "invoices")
    # One logical invoice: keyed writes collapse to one per key, unkeyed
    # writes each make an invoice.
    effective_invoices = len({r["key"] for r in invoices if r["key"]}) + sum(1 for r in invoices if not r["key"])
    return {
        "mode": mode, "tool": tool, "point": point, "n": n, "killed": killed,
        "finished": phase2.get("final") == "FINISHED", "phase2_error": p2.stderr[-400:] if p2.returncode else "",
        "emails": len(sent), "invoices": effective_invoices, "invoice_calls": len(invoices),
        "left_for_a_human": phase2.get("pending", []),
    }


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--runs", type=int, default=20, help="trials per mode, tool and kill point")
    p.add_argument("--parallel", type=int, default=8)
    p.add_argument("--out", default=str(HERE / "results"))
    p.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    for flag in ("--mode", "--agent", "--world", "--phase", "--kill"):
        p.add_argument(flag, help=argparse.SUPPRESS)
    args = p.parse_args()
    if args.worker:
        worker(args)
        return

    server = os.environ.get("POLIGN_SERVER") or "polign-server"
    root = Path(tempfile.mkdtemp(prefix="kill-actions-"))
    proc, url = start_server(server, root / "data")
    env = {**os.environ, "POLIGN_URL": url, "POLIGN_COLLECTION": "kill_actions"}
    out = Path(args.out) / time.strftime("kill-actions-%Y%m%d-%H%M%S")
    out.mkdir(parents=True)
    plan = [(m, t, k, n) for m in MODES for t in TOOLS for k in POINTS for n in range(args.runs)]
    rows: list[dict] = []
    try:
        with ThreadPoolExecutor(args.parallel) as pool, open(out / "runs.jsonl", "w") as f:
            for row in pool.map(lambda c: trial(*c, root / "worlds", env), plan):
                rows.append(row)
                f.write(json.dumps(row) + "\n")
                f.flush()
    finally:
        proc.terminate()
        proc.wait(timeout=20)

    summary = []
    print(f"{'mode':<17} {'tool':<13} {'kill point':<10} {'runs':>4} {'dup emails':>10} {'lost emails':>11} "
          f"{'dup invoices':>12} {'for a human':>11} {'unfinished':>10}")
    for m in MODES:
        for t in TOOLS:
            for k in POINTS:
                cell = [r for r in rows if (r["mode"], r["tool"], r["point"]) == (m, t, k)]
                s = {
                    "mode": m, "tool": t, "point": k, "runs": len(cell),
                    "not_killed": sum(not r["killed"] for r in cell),
                    "duplicate_emails": sum(max(0, r["emails"] - 1) for r in cell),
                    "lost_emails": sum(r["emails"] == 0 and "send_email" not in r["left_for_a_human"] for r in cell),
                    "duplicate_invoices": sum(max(0, r["invoices"] - 1) for r in cell),
                    "left_for_a_human": sum(bool(r["left_for_a_human"]) for r in cell),
                    "unfinished": sum(not r["finished"] for r in cell),
                }
                summary.append(s)
                print(f"{m:<17} {t:<13} {k:<10} {s['runs']:>4} {s['duplicate_emails']:>10} {s['lost_emails']:>11} "
                      f"{s['duplicate_invoices']:>12} {s['left_for_a_human']:>11} {s['unfinished']:>10}")
    (out / "summary.json").write_text(json.dumps(summary, indent=2))
    bad = [s for s in summary if s["not_killed"]]
    if bad:
        print(f"warning: {sum(s['not_killed'] for s in bad)} trials were not killed at their point", file=sys.stderr)
    print(f"\n{len(rows)} trials; results in {out}")


if __name__ == "__main__":
    main()
