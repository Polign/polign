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
