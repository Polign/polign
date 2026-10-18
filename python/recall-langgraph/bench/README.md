# Resume gate

Does an agent that dies at a random point, and is resumed from its Recall
records, finish its task without spending much more than one that was never
interrupted?

`gate.py` answers that with a real model and a real task. The agent migrates
`billing.charge(x)` calls to `billing.charge_v2(x, currency="USD")` across a
21-file repository, one tool call per turn, using file tools and a
`run_tests` tool that checks every file exactly. Some files are decoys that
must be left alone.

Each trial is one of:

- **baseline**: one process runs the task start to finish.
- **crashed**: a process dies at a chosen model call, the way a pod does. Its
  Recall subprocess is killed and nothing is released, and the reply it just
  generated is lost. A new process resumes the agent with
  [recall-langgraph](../README.md) on a fresh LangGraph thread, waits out the
  dead process's lease, and finishes from the briefing alone.

Files the agent wrote before the crash stay on disk, the way work pushed to a
branch or a bucket would. Every model call's token usage, in every process,
is logged, along with the tools it called and the briefing each resumed
process received.

The gate passes when crashed runs succeed about as often as baselines and use
under 10% more tokens on average.

## Running it

```bash
pip install recall-langgraph langchain-openai
export OPENAI_API_KEY=...
python gate.py --model gpt-4.1-mini --baselines 10 --crashed 20 --parallel 4 --budget 3
```

The first three baselines run one at a time to learn how long a run is. Kill
points are then spread evenly from the second model call to 90% of a typical
run, and the remaining trials run in random order so changes in the API
during the run affect both kinds alike.

Spend is estimated from token counts at list prices, with cached input
counted at full price, and no trial starts once the estimate reaches
`--budget`. A gpt-4.1-mini run costs about $0.05.

Results go to `results/<model>-<time>/`: `runs.jsonl` has one row per trial,
`summary.json` the totals, and each trial's `ledger.jsonl` every model call.

# Kill test

Does an agent killed in the middle of a tool call ever repeat a side effect,
or lose one, once it is resumed? `kill_actions.py` answers that with no model
and no API key, so it is free to run and gives the same answer every time.

The agent is a scripted, cautious policy. It calls `lookup_order` (pure),
`save_invoice` (idempotent) and `send_email` (external) in turn. It skips a
tool whose result it can see, and it does not repeat a call that its briefing
says may already have happened. What it can see is exactly what Recall hands
a resumed agent, so the test measures the harness, not how clever a model is.

Each trial kills the first process, together with its Recall subprocess,
with SIGKILL at one of three points:

- **before**: the call is recorded but has not taken effect;
- **after**: it took effect but the tool has not returned;
- **returned**: the tool returned and the next model call never starts.

A second process then resumes the agent and finishes. The tools append to
files outside both processes, standing in for a mail server and an invoice
store. Three setups are compared: `plain` (recall-langgraph resume with
ordinary tools and no keys), `durable` (`RecallResume.durable`, no
resolver) and `durable+resolver` (with a resolver that checks the mail log).

```bash
POLIGN_SERVER=/path/to/polign-server RECALL_BIN=/path/to/recall \
    python kill_actions.py --runs 20 --parallel 12
```

Results from 2026-10-17: 540 trials, 20 per cell. Every trial was killed at
its point, and every resumed run finished.

| Setup | Duplicate emails | Duplicate invoices | Lost emails | Left for a person to check |
|---|---|---|---|---|
| plain | 20 of 60 email kills | 20 of 60 invoice kills | 0 | 0 |
| durable | 0 | 0 | 0 | 40 (email killed before or after sending) |
| durable+resolver | 0 | 0 | 0 | 0 |

In the plain setup every duplicate comes from the **after** point, where
the effect happened but its result was never recorded. With durable tools,
the interrupted invoice is re-run with its original key, so the store keeps
one. An interrupted email is never re-sent blindly. Without a resolver it
waits at the top of the briefing for someone to check. With the resolver,
the mail log settles it: emails already sent are recorded as done, and
emails never sent are recorded as failed and then sent once.

`results/kill-actions-<time>/` holds `runs.jsonl` (one row per trial) and
`summary.json`.

## Kill test with a real model

`--model claude-opus-5-5` replaces the scripted policy with Claude Opus 5.5 at
its default effort. The system prompt tells it that its process can be
restarted, never to do twice what the customer would notice twice, and to
report anything it cannot check instead of repeating it. The task, tools,
kill points and counting are the same as above.

Results from 2026-10-18: 135 trials, 5 per setup, tool and kill point, on
polign_db. Every trial was killed at its point and every resumed run ended.
Estimated cost $11.22.

| Setup | Duplicate emails | Duplicate invoices | Emails never sent | Of those, recorded as unconfirmed |
|---|---|---|---|---|
| Ordinary tools | 0 of 15 | 0 of 15 | 5 of 15 | 0 |
| Durable tools, no resolver | 0 | 0 | 5 of 15 | 5 |
| Durable tools with a resolver | 0 | 0 | **0** | n/a |

With this prompt Opus did not repeat a call it could not confirm, with or
without durable tools, so no setup sent an email twice. The difference is
what happens to an email that never went out. Killed after the email call
started but before it took effect, Opus held back every time and the email
was never sent. With ordinary tools nothing recorded that: in 4 of the 5
runs Opus replied FINISHED, and the fifth asked a person to check. With
durable tools all 5 stayed open in the records as unconfirmed, even where
Opus replied FINISHED. With the resolver reading the mail log, all 15 email
kills ended with exactly one email sent.

Opus's FINISHED is not proof that every action was settled: in 9 of the 10
durable runs that left the email unconfirmed it still replied FINISHED.
Check `pending_actions`, not the reply.
