# recall-langgraph

Resume [LangGraph](https://github.com/langchain-ai/langgraph) agents from
their own records, backed by [Recall by Polign](https://polign.com/recall.html).

A long-running agent that crashes, gets rescheduled, or moves to another
machine usually starts over, or restores an exact snapshot of its state. With
this package it picks up where it left off from what it wrote down instead:
while it works, it keeps a working state (its goal, plan, progress and
decisions) and pointers to where its work lives, and every message of the run
is recorded. The next process that opens the same agent id gets a fresh
starting context built from those records, sized to a token budget.

This sits beside your checkpointer, not in place of it. LangGraph's
checkpointer still handles threads and interrupts however you configure it.

```bash
pip install recall-langgraph
```

pip also brings the [`polign_db`](https://pypi.org/project/polign-db/)
package with the `polign` CLI and `polign-server` binaries for Linux, macOS
and Windows, so there is no separate database to download.

## Usage with create_react_agent

```python
from langchain_openai import ChatOpenAI
from langgraph.prebuilt import create_react_agent
from recall_langgraph import RecallResume, recall_tools

with RecallResume("billing-migrator", local_dir="./recall-data") as resume:
    agent = create_react_agent(
        ChatOpenAI(model="gpt-4.1"),
        tools=[*your_tools, *recall_tools(resume)],
        prompt=(
            "You move the billing service to the v2 API. Keep your working state "
            "current with update_working_state, and call milestone when a step is done."
        ),
        pre_model_hook=resume.pre_model_hook,
        post_model_hook=resume.post_model_hook,
    )
    agent.invoke({"messages": [("user", "Carry on with the migration.")]})
```

Run it, stop it halfway, and run it again: the second run starts from the
working state, pointers and recent turns the first one wrote.

What happens:

1. Entering the `with` block resumes the agent. It takes a lease on the id,
   so only one process acts for the agent at a time, and reads back its
   records. The first time an id is used, it starts fresh.
2. Before every model call, `pre_model_hook` puts one `SystemMessage` in
   front of the messages, the briefing: the working state, pointers to the
   work, relevant memories, and the most recent turns, within
   `token_budget`. It goes to the model as `llm_input_messages`, so the
   graph's `messages` state never holds it.
3. The hooks record each new message once, in order, as a turn: human
   messages before the model call, the model's reply right after it, and tool
   results before the next call. A reply with tool calls is recorded as the
   calls themselves, `name(arg=value)`, without call ids. In the briefing,
   arguments longer than 200 characters are shortened to their length (the
   record keeps them whole), since what a long argument produced usually
   survives it. A turn longer than the output threshold is stored whole as an
   output, and the briefing shows a reference the model can pass to
   `fetch_output`.
4. The model keeps its working state current through the tools from
   `recall_tools`.
5. Leaving the block (or calling `release()`) hands the lease over, so the
   next process can resume at once. A process that dies without releasing
   holds the lease until it expires (60 seconds by default).

Recall never replays old messages into the state: the briefing already
carries the recent turns as text, and a replayed tool result without its
matching tool call would be refused by the model API. On a new `thread_id`,
the resumed run starts from the briefing alone. On the old `thread_id`, a
persistent checkpointer restores the old messages as well, and nothing is
recorded twice: each turn keeps its message id, so the first recording after
a resume skips every restored message up to the last one Recall already has.
Messages after that one, such as the caller's new input or a reply the dead
process never recorded, are recorded as usual. The model then sees the
restored history and the briefing both; start a new thread if you would
rather it saw only the briefing.

`create_react_agent` is deprecated in LangGraph 1.0 in favor of
`langchain.agents.create_agent`, but it still ships in `langgraph.prebuilt`
and the hooks work with it. Use `version="v2"` (the default), since
`post_model_hook` needs it.

## Your own StateGraph

```python
from langgraph.graph import START, MessagesState, StateGraph

def call_model(state: MessagesState):
    return {"messages": [model.invoke(resume.with_briefing(state["messages"]))]}

builder = StateGraph(MessagesState)
builder.add_node("model", call_model)
builder.add_node("record", resume.record_node)
builder.add_edge(START, "model")
builder.add_edge("model", "record")
```

- `with_briefing(messages)` returns the messages with the briefing in front,
  for the model's input, and leaves the state alone.
- `record_node` records every message not recorded yet and changes nothing.
  Put it after each node that adds messages. `record(messages)` does the same
  from your own code.
- `briefing_node` adds the briefing to the `messages` state itself, once per
  thread, if you would rather keep it there. Put it first.

The hooks and nodes work with `invoke` and `ainvoke`.

## The tools

| Tool | What the model uses it for |
|---|---|
| `update_working_state` | Save its goal, plan, progress, focus, decisions, open questions and notes. Fields it leaves out are kept. |
| `milestone` | Declare a durable point, such as tests passing. |
| `fetch_output` | Read a stored output in full by its ref. |
| `set_pointer` | Record where a piece of work lives: a git branch or commit, an object, an environment, an external item, or a process. |

A failed call comes back to the model as the tool's text. Your own code can
call `resume.update_working_state`, `resume.milestone`, `resume.fetch_output`
and `resume.set_pointer`, and `resume.agent` is the underlying
`polign_recall.ResumedAgent` for everything else (`record_turn`,
`recent_turns`, `store_output`, `pointers`, `working_state_history`).
`resume.context` is what the resume returned, and `resume.briefing` its text.

## Options

| Option | Default | What it does |
|---|---|---|
| `client` | none | A `polign_recall.Client` opened with `agent=True`, to share one subprocess. Without it, `RecallResume` opens its own and closes it on release. |
| `local_dir` | none | Keep the database in this directory and run its server (only when `RecallResume` opens its own client) |
| `env` | process environment | `POLIGN_URL`, `POLIGN_API_KEY` and the rest, for a server you run yourself |
| `token_budget` | 8000 | Bound on the briefing, in tokens |
| `lease_ttl` | 60 | Seconds each lease epoch lasts; renewed in the background |
| `holder` | host, pid and a random suffix | Names this process in lease records |
| `output_threshold` | 2000 | Turns longer than this many tokens are stored as outputs |

## Errors

If another process holds the agent, resuming raises
`polign_recall.RecallError` with code `"lease_held"`. Wait and try again, or
stop. If another process takes the agent over mid-run, the next record fails
with code `"lease_lost"`, which stops the graph run; this process should
stop acting for the agent.

Records are append-only. Removing or trimming messages in the graph state
changes what the model sees, never what was recorded.

## Where the records are stored

`local_dir="./recall-data"` keeps the database on this machine; the first
process to open the directory starts a `polign-server` for it in the
background, and later ones share it. Agents that move between machines need
one shared server: run `polign-server` where they can all reach it
([Get started](https://polign.com/developers.html) shows how, including
storing into S3, GCS or Azure) and pass its address through `env` or the
environment:

```python
RecallResume(
    "billing-migrator",
    env={"POLIGN_URL": "http://memory.internal:23000", "POLIGN_API_KEY": key},
)
```

The agent's records live in their own collection next to the memory
collection (`<collection>_agents`).

## Development

```bash
cd python/recall-langgraph
python -m pip install -e . pytest pytest-asyncio
pytest tests/unit_tests -q                # a fake Recall client, no network
pytest tests/integration_tests -v         # real polign-server and polign CLI
```

The integration tests locate the binaries like the other packages' tests do
(`POLIGN_SERVER`, `POLIGN_SOURCE`, `POLIGN_SERVER_VERSION`, or the latest
release download) and skip when none is found or when the CLI has no
`polign mcp -agent`. Both suites drive real compiled graphs with a scripted
chat model, so no model API key is needed.
