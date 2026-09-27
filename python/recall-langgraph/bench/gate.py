"""The Phase 1 gate: does an agent killed at a random point, then resumed from
its Recall records, finish its task without spending much more than an agent
that was never interrupted?

Each trial runs one real task with a real model:

  baseline  one process, start to finish.
  crashed   a process that dies at a random model call, the way a pod does:
            its Recall subprocess is SIGKILLed and nothing is released. A new
            process then resumes the agent through recall-langgraph and
            finishes, on a fresh LangGraph thread, from the briefing alone.

The workspace on disk is the agent's "work pushed out": files it wrote before
the crash are still there, as a branch or a bucket would be. Token usage of
every model call, in every process, is logged. The gate passes when crashed
runs succeed as often as baselines and use under 10% more tokens on average.

    python gate.py --model gpt-4.1-mini --baselines 10 --crashed 20 --budget 5

The key is read from OPENAI_API_KEY. Spend is estimated from token counts at
list prices (cached input counted at full price), and the run stops once the
estimate reaches --budget.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import random
import shutil
import statistics
import subprocess
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent

# Dollars per million tokens (input, output). List prices at the time of
# writing; pass --price to override.
PRICES = {
    "gpt-4.1": (2.00, 8.00),
    "gpt-4.1-mini": (0.40, 1.60),
    "gpt-4.1-nano": (0.10, 0.40),
    "gpt-4o": (2.50, 10.00),
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-5": (1.25, 10.00),
    "gpt-5-mini": (0.25, 2.00),
}

# --- the task ----------------------------------------------------------------

def _files() -> dict[str, str]:
    """A small shop codebase: 16 files with calls to migrate, and decoys that
    must be left alone (another function named like it, an already migrated
    call, docs and comments that mention it)."""
    files = {
        "README.md": "# shop\n\nPayments go through `billing`. `billing.charge(amount)` is deprecated;\n"
                     "use `billing.charge_v2(amount, currency=...)` instead.\n",
        "api/refunds.py": "import billing\n\n\ndef refund(order):\n    # refunds go through billing.refund, not billing.charge(...)\n"
                          "    return billing.refund(order.total)\n",
        "worker/reports.py": "def summarize(rows):\n    return {\"count\": len(rows), \"sum\": sum(r.amount for r in rows)}\n",
        "wallet/topup.py": "import wallet\n\n\ndef top_up(user, amount):\n    return wallet.recharge(user, amount)\n",
        "api/tax.py": "import billing\n\n\ndef charge_with_tax(amount, rate):\n"
                      "    return billing.charge_v2(amount * (1 + rate), currency=\"USD\")\n",
    }
    names = ["checkout", "subscriptions", "invoices", "retries", "marketplace", "gift_cards", "shipping",
             "donations", "late_fees", "trials", "addons", "installments", "tips", "deposits", "penalties",
             "overages"]
    bodies = [
        "    total = sum(i.price for i in cart)\n    return billing.charge(total)\n",
        "    fee = item.plan.monthly_fee\n    receipt = billing.charge(fee)\n    item.last_receipt = receipt\n    return receipt\n",
        "    for line in item.lines:\n        billing.charge(line.amount)\n    item.settled = True\n",
        "    done = []\n    for p in item:\n        if p.failed:\n            done.append(billing.charge(p.amount))\n    return done\n",
    ]
    for i, name in enumerate(names):
        folder = ("api", "worker", "admin")[i % 3]
        doc = '    """Takes payment. See billing.charge() in the README."""\n' if i % 5 == 0 else ""
        files[f"{folder}/{name}.py"] = f"import billing\n\n\ndef {name}(item, cart=()):\n{doc}{bodies[i % len(bodies)]}"
    return files


FILES = _files()

TASK = (
    "In this repository, every call `billing.charge(X)` must become "
    '`billing.charge_v2(X, currency="USD")`, keeping X exactly as written. Do not change '
    "anything else: not comments, not docs, not other billing functions. Work file by file. "
    "When run_tests passes, reply with the single word DONE."
)

SYSTEM = (
    "You are a careful coding agent. Keep your working state current with update_working_state "
    "(goal, plan, progress, focus) when your plan or progress changes, and call milestone when a "
    "group of files is done, so that if this process dies, the next one can continue from your "
    "notes. Files already on disk are real: if you are resuming, do not redo work they show is done."
)


def expected(text: str) -> str:
    """The file after a correct migration: every call rewritten, and docs,
    comments and docstrings that only mention billing.charge left alone."""
    import re
    out = []
    for line in text.splitlines(keepends=True):
        prose = line.lstrip().startswith(("#", '"""')) or "`" in line
        out.append(line if prose else re.sub(
            r"billing\.charge\(([^()]*(?:\([^()]*\))?[^()]*)\)", r'billing.charge_v2(\1, currency="USD")', line))
    return "".join(out)


def check(workspace: Path) -> list[str]:
    """Paths that are not exactly as the migration should leave them."""
    bad = []
    for rel, original in FILES.items():
        path = workspace / rel
        want = original if rel.endswith(".md") else expected(original)
        if not path.exists() or path.read_text() != want:
            bad.append(rel)
    return bad


# --- one agent process -------------------------------------------------------

def worker(args: argparse.Namespace) -> None:
    """Run (or resume) the agent until it finishes, or die at --kill-at."""
    from langchain_core.callbacks import BaseCallbackHandler
    from langchain_core.tools import tool
    from langchain_openai import ChatOpenAI
    from langgraph.prebuilt import create_react_agent
    from polign_recall import RecallError
    from recall_langgraph import RecallResume, recall_tools

    workspace = Path(args.workspace)
    ledger = open(args.ledger, "a", buffering=1)
    state = {"calls": 0}

    def within(rel: str) -> Path:
        path = (workspace / rel).resolve()
        if workspace.resolve() not in path.parents:
            raise ValueError("path outside the repository")
        return path

    @tool
    def list_files() -> str:
        """List every file in the repository."""
        return "\n".join(sorted(str(p.relative_to(workspace)) for p in workspace.rglob("*") if p.is_file()))

    @tool
    def read_file(path: str) -> str:
        """Read one file."""
        return within(path).read_text()

    @tool
    def write_file(path: str, content: str) -> str:
        """Replace one file's whole content."""
        within(path).write_text(content)
        return f"wrote {path}"

    @tool
    def run_tests() -> str:
        """Check the migration. Lists the files that are not right yet."""
        bad = check(workspace)
        return "PASS" if not bad else "FAIL: " + ", ".join(bad)

    # The orchestrator started one Recall server; every worker connects to it
    # the way pods would share one, through POLIGN_URL and POLIGN_API_KEY.
    resume = RecallResume(args.agent, lease_ttl=5, token_budget=6000)
    deadline = time.monotonic() + 30
    while True:
        try:
            resume.open()
            break
        except RecallError as exc:
            # The crashed process's lease runs out within its TTL.
            if exc.code != "lease_held" or time.monotonic() > deadline:
                raise
            time.sleep(1)

    ledger.write(json.dumps({"phase": args.phase, "resumed_fresh": resume.context.fresh,
                             "briefing": resume.briefing}) + "\n")

    class Meter(BaseCallbackHandler):
        def on_llm_end(self, response, **kwargs):
            usage = {}
            for gens in response.generations:
                for g in gens:
                    message = getattr(g, "message", None)
                    meta = getattr(message, "usage_metadata", None) or {}
                    calls = [f"{c['name']}({c['args'].get('path', c['args'].get('name', ''))})"
                             for c in getattr(message, "tool_calls", None) or []]
                    usage = {"in": meta.get("input_tokens", 0), "out": meta.get("output_tokens", 0), "tools": calls}
            state["calls"] += 1
            ledger.write(json.dumps({"phase": args.phase, "call": state["calls"], **usage}) + "\n")
            ledger.flush()
            if args.kill_at and state["calls"] >= args.kill_at:
                # Die like a pod: the reply just generated is never recorded,
                # its tool calls never run, and nothing is released.
                resume._client._process.kill()
                os._exit(137)

    tools = [list_files, read_file, write_file, run_tests, *recall_tools(resume)]
    # One tool call per turn, the way a longer real task unfolds; with
    # parallel calls the model rewrites everything in one reply and there is
    # no middle for a crash to land in.
    model = ChatOpenAI(model=args.model, temperature=0, callbacks=[Meter()], max_retries=12)
    model = model.bind_tools(tools, parallel_tool_calls=False)
    agent = create_react_agent(
        model, tools,
        prompt=SYSTEM, pre_model_hook=resume.pre_model_hook, post_model_hook=resume.post_model_hook,
    )
    first = resume.context.fresh
    message = TASK if first else "Continue the task from where you left off."
    result = agent.invoke({"messages": [("user", message)]},
                          {"configurable": {"thread_id": f"{args.phase}"}, "recursion_limit": 300})
    final = result["messages"][-1].content if result["messages"] else ""
    ledger.write(json.dumps({"phase": args.phase, "final": str(final)[:200]}) + "\n")
    resume.release()


# --- the orchestrator --------------------------------------------------------

def spend(ledger: Path, model: str, prices: tuple[float, float]) -> tuple[int, int, float]:
    tin = tout = 0
    if ledger.exists():
        for line in ledger.read_text().splitlines():
            row = json.loads(line)
            tin += row.get("in", 0)
            tout += row.get("out", 0)
    return tin, tout, tin / 1e6 * prices[0] + tout / 1e6 * prices[1]


def run_trial(kind: str, n: int, args: argparse.Namespace, root: Path, kill_at: int) -> dict:
    trial = root / f"{kind}-{n:03d}"
    shutil.rmtree(trial, ignore_errors=True)
    workspace = trial / "repo"
    for rel, text in FILES.items():
        (workspace / rel).parent.mkdir(parents=True, exist_ok=True)
        (workspace / rel).write_text(text)
    ledger = trial / "ledger.jsonl"
    agent = f"gate-{kind}-{n:03d}-{os.getpid()}"
    base = [sys.executable, __file__, "--worker", "--model", args.model, "--agent", agent,
            "--workspace", str(workspace), "--ledger", str(ledger)]
    started = time.monotonic()
    if kind == "crashed":
        subprocess.run(base + ["--phase", "p1", "--kill-at", str(kill_at)], timeout=900)
        subprocess.run(base + ["--phase", "p2"], timeout=900)
    else:
        subprocess.run(base + ["--phase", "p1"], timeout=900)
    tin, tout, cost = spend(ledger, args.model, args.prices)
    calls = sum(1 for line in ledger.read_text().splitlines() if '"call"' in line) if ledger.exists() else 0
    return {"kind": kind, "n": n, "kill_at": kill_at, "ok": not check(workspace), "calls": calls,
            "tokens": tin + tout, "in": tin, "out": tout, "cost": cost,
            "seconds": round(time.monotonic() - started, 1)}


def start_recall(directory: Path) -> None:
    """Start one local Recall server for the whole run and point every worker
    at it through the environment they inherit."""
    from polign_recall.client import polign_bin
    polign = polign_bin()
    subprocess.run([polign, "recall", "setup", "-local", "-no-plugin", "-config-dir", str(directory)],
                   check=True, capture_output=True, env={k: v for k, v in os.environ.items() if not k.startswith("POLIGN_")})
    os.environ["POLIGN_URL"] = json.loads((directory / "runtime.json").read_text())["url"]
    os.environ["POLIGN_API_KEY"] = (directory / "local-key").read_text().strip()


def summarize(rows: list[dict]) -> dict:
    out = {}
    for kind in ("baseline", "crashed"):
        rs = [r for r in rows if r["kind"] == kind]
        ok = [r for r in rs if r["ok"]]
        out[kind] = {
            "runs": len(rs), "succeeded": len(ok),
            "mean_tokens": round(statistics.mean(r["tokens"] for r in ok)) if ok else None,
            "median_tokens": round(statistics.median(r["tokens"] for r in ok)) if ok else None,
            "stdev_tokens": round(statistics.stdev(r["tokens"] for r in ok)) if len(ok) > 1 else None,
        }
    b, c = out["baseline"]["mean_tokens"], out["crashed"]["mean_tokens"]
    if b and c:
        out["extra_tokens_pct"] = round(100 * (c - b) / b, 1)
        out["gate_passed"] = out["extra_tokens_pct"] < 10 and \
            out["crashed"]["succeeded"] / max(1, out["crashed"]["runs"]) >= \
            out["baseline"]["succeeded"] / max(1, out["baseline"]["runs"]) - 0.1
    return out


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--model", default="gpt-4.1-mini")
    p.add_argument("--baselines", type=int, default=10)
    p.add_argument("--crashed", type=int, default=20)
    p.add_argument("--budget", type=float, default=5.0, help="stop once estimated spend reaches this many dollars")
    p.add_argument("--price", help="input,output dollars per million tokens, for a model not in the table")
    p.add_argument("--out", default=str(HERE / "results"))
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--parallel", type=int, default=1, help="trials to run at once")
    p.add_argument("--kill-max", type=int, default=0,
                   help="spread kill points from call 2 to this call instead of across the whole run")
    # Worker-only flags.
    p.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    for flag in ("--agent", "--workspace", "--ledger", "--phase"):
        p.add_argument(flag, help=argparse.SUPPRESS)
    p.add_argument("--kill-at", type=int, default=0, help=argparse.SUPPRESS)
    args = p.parse_args()
    if args.worker:
        worker(args)
        return
    if not os.environ.get("OPENAI_API_KEY"):
        sys.exit("set OPENAI_API_KEY")
    args.prices = tuple(float(x) for x in args.price.split(",")) if args.price else PRICES.get(args.model)
    if not args.prices:
        sys.exit(f"no price for {args.model}; pass --price input,output")

    root = Path(args.out) / f"{args.model}-{time.strftime('%Y%m%d-%H%M%S')}"
    root.mkdir(parents=True)
    start_recall(root / "recall")
    rng = random.Random(args.seed)
    rows: list[dict] = []
    total = [0.0]
    lock = threading.Lock()

    def record(row: dict) -> None:
        with lock:
            rows.append(row)
            total[0] += row["cost"]
            with open(root / "runs.jsonl", "a") as f:
                f.write(json.dumps(row) + "\n")
            print(f"{row['kind']:8} #{row['n']:<3} ok={row['ok']!s:5} calls={row['calls']:<3} "
                  f"kill_at={row['kill_at']:<3} tokens={row['tokens']:<7} ${row['cost']:.3f}  "
                  f"total ${total[0]:.2f}", flush=True)

    # A few baselines first, one at a time, to learn how long a run is, so
    # the kill points spread over the whole run.
    warmup = min(3, args.baselines)
    for n in range(warmup):
        if total[0] < args.budget:
            record(run_trial("baseline", n, args, root, 0))
    done = [r["calls"] for r in rows if r["ok"]] or [20]
    typical = statistics.median(done)
    plan = [("baseline", n, 0) for n in range(warmup, args.baselines)]
    # Kill points spread evenly over the run, from the second call to 90%
    # of a typical run, then shuffled together with the baselines so drift
    # in the API affects both kinds alike.
    hi = args.kill_max or max(2, int(typical * 0.9))
    plan += [("crashed", n, 2 + round(n * (hi - 2) / max(1, args.crashed - 1))) for n in range(args.crashed)]
    rng.shuffle(plan)

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.parallel) as pool:
        pending = set()
        for kind, n, kill_at in plan:
            # Budget is checked as trials start, so the overshoot is at most
            # the trials already running.
            if total[0] >= args.budget:
                print(f"budget of ${args.budget:.2f} reached; not starting more trials")
                break
            pending.add(pool.submit(lambda k=kind, i=n, at=kill_at: record(run_trial(k, i, args, root, at))))
            while len(pending) >= args.parallel:
                finished, pending = concurrent.futures.wait(pending, return_when=concurrent.futures.FIRST_COMPLETED)
                for f in finished:
                    f.result()
        for f in concurrent.futures.as_completed(pending):
            f.result()
    total = total[0]
    summary = {"model": args.model, "spent_usd": round(total, 2), **summarize(rows)}
    (root / "summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
