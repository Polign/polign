"""One coding agent, run two ways for the demo.

RECALL=on   the agent records its turns and notes in Polign Recall. After a
            crash, a new container (even a newer image with a newer model)
            resumes from those records.
RECALL=off  the agent keeps everything in process memory, the way an agent
            protected only by container snapshots does.

Every model call prints one line, prefixed with the image version and the
model actually answering, so the recording shows which agent is running.
The files it edits live in /work, a volume, the way a real agent's work lives
in a repository or a bucket.
"""

import json
import os
import sys
import time
from pathlib import Path

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from langgraph.prebuilt import create_react_agent

from task import FILES, TASK, check

VERSION = os.environ.get("AGENT_VERSION", "v1")
MODEL = os.environ.get("MODEL", "gpt-4.1-mini")
AGENT_ID = os.environ.get("AGENT_ID", "demo-agent")
RECALL = os.environ.get("RECALL", "on") == "on"
WORK = Path(os.environ.get("WORKSPACE", "/work"))
TAG = f"[{VERSION} · {MODEL}]"

SYSTEM = (
    "You are a careful coding agent. Keep your working state current with update_working_state "
    "(goal, plan, progress, focus) when your plan or progress changes, and call milestone when a "
    "group of files is done. Files already on disk are real: if you are resuming, do not redo "
    "work they show is done."
)


def say(line: str) -> None:
    print(f"{TAG} {line}", flush=True)


def main() -> None:
    # The key comes from a file mounted read-only from RAM on the host, so it
    # is never part of an image or a container's saved configuration.
    key_file = Path("/run/secrets/openai.key")
    if not os.environ.get("OPENAI_API_KEY") and key_file.exists():
        os.environ["OPENAI_API_KEY"] = key_file.read_text().strip()
    repo = WORK / "repo"
    if not repo.exists():
        for rel, text in FILES.items():
            (repo / rel).parent.mkdir(parents=True, exist_ok=True)
            (repo / rel).write_text(text)
    ledger = open(WORK / "ledger.jsonl", "a", buffering=1)

    def within(rel: str) -> Path:
        path = (repo / rel).resolve()
        if repo.resolve() not in path.parents:
            raise ValueError("path outside the repository")
        return path

    @tool
    def list_files() -> str:
        """List every file in the repository."""
        return "\n".join(sorted(str(p.relative_to(repo)) for p in repo.rglob("*") if p.is_file()))

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
        bad = check(repo)
        return "PASS" if not bad else "FAIL: " + ", ".join(bad)

    tools = [list_files, read_file, write_file, run_tests]
    hooks = {}
    resumed = False
    resume = None
    if RECALL:
        from polign_recall import RecallError
        from recall_langgraph import RecallResume, recall_tools

        resume = RecallResume(AGENT_ID, lease_ttl=10, token_budget=6000)
        deadline = time.monotonic() + 60
        while True:
            try:
                resume.open()
                break
            except RecallError as exc:
                if exc.code != "lease_held" or time.monotonic() > deadline:
                    raise
                say("previous container's lease is still live; waiting")
                time.sleep(2)
        resumed = not resume.context.fresh
        tools += recall_tools(resume)
        hooks = {"pre_model_hook": resume.pre_model_hook, "post_model_hook": resume.post_model_hook}

    step = {"n": 0}

    class Meter(BaseCallbackHandler):
        def on_llm_end(self, response, **kwargs):
            for gens in response.generations:
                for g in gens:
                    msg = getattr(g, "message", None)
                    meta = getattr(msg, "usage_metadata", None) or {}
                    calls = [f"{c['name']}({c['args'].get('path', '')})" for c in getattr(msg, "tool_calls", None) or []]
                    step["n"] += 1
                    ledger.write(json.dumps({"version": VERSION, "model": MODEL, "in": meta.get("input_tokens", 0),
                                             "out": meta.get("output_tokens", 0)}) + "\n")
                    say(f"call {step['n']}: {', '.join(calls) or 'reply'}")

    if RECALL:
        say(f"resumed from Recall: {resumed} (turns on record: {resume.context.turn_seq})")
    else:
        say("starting from process memory only")

    extra = {}
    if os.environ.get("HTTP_KEEPALIVE", "on") == "off":
        # Close the connection to the model API after every call. CRIU
        # cannot checkpoint a process holding an open TCP connection unless
        # it is told to try to restore that connection too, so an agent that
        # relies on container snapshots has to run this way.
        import httpx
        extra["http_client"] = httpx.Client(limits=httpx.Limits(max_keepalive_connections=0), timeout=120)
    model = ChatOpenAI(model=MODEL, temperature=0, callbacks=[Meter()], max_retries=12, **extra)
    model = model.bind_tools(tools, parallel_tool_calls=False)
    agent = create_react_agent(model, tools, prompt=SYSTEM, **hooks)
    message = "Continue the task from where you left off." if resumed else TASK
    agent.invoke({"messages": [("user", message)]}, {"recursion_limit": 300})
    bad = check(repo)
    say("DONE: run_tests PASS" if not bad else f"stopped with {len(bad)} files wrong")
    if resume is not None:
        resume.release()


if __name__ == "__main__":
    sys.exit(main())
