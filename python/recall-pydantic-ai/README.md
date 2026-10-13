# recall-pydantic-ai

Long-term memory for [Pydantic AI](https://pydantic.dev/docs/ai/) agents, backed by
[Recall by Polign](https://polign.com/recall.html).

Every run starts with what the agent knows about the user in its
instructions, and the model saves new facts through a `remember` tool. Facts
are typed: each one is a predicate from a closed registry with a value, so
"call me Sam" replaces the old name instead of piling up a second one, and the
history of the change is kept. No second model runs on the read path, and no
embedding service is needed.

```bash
pip install recall-pydantic-ai
```

That is the whole install. pip also brings the `recall` binary and
`polign-server` for Linux, macOS and Windows, so there is no separate database
to download.

## Usage

`RecallMemory` is a Pydantic AI
[capability](https://pydantic.dev/docs/ai/capabilities/overview/). Add it to an agent:

```python
from dataclasses import dataclass

from polign_recall import Client
from pydantic_ai import Agent
from recall_pydantic_ai import RecallMemory


@dataclass
class Deps:
    user_id: str


recall = Client(local_dir="./recall-data", typed=True)   # one per process; the database lives here

agent = Agent(
    "anthropic:claude-sonnet-5-5",
    deps_type=Deps,
    instructions="You help engineers with their projects.",
    capabilities=[RecallMemory(recall, subject=lambda ctx: ctx.deps.user_id)],
)

agent.run_sync("I'm Alex. We deploy to Fly.io.", deps=Deps("user-42"))
# a later run, in a new process or a week later
result = agent.run_sync("Where do we deploy again?", deps=Deps("user-42"))
print(result.output)   # Fly.io
```

What happens:

1. At the start of each run, `RecallMemory` loads the user's current facts
   and adds them to the instructions inside a `<recall_memory>` block.
2. The user says "I'm Alex. We deploy to Fly.io." The model calls `remember`
   twice, with `name` and `deployment_target`.
3. Later the user says "actually it's Sam". The model calls `remember` again,
   Recall replaces the old name, and the tool tells the model
   `Updated name to Sam; it was Alex.`
4. The next run's instructions say Sam. Alex is still in the record:
   `recall.history("user-42", "name")` lists both.

Nothing is searched on each model request. A user's typed facts are a short
list, so the whole set fits in the instructions, and they stay the same for
the whole run, which keeps the provider's prompt cache valid. When a user has
more facts than `limit` (default 20), the run also searches for the prompt,
adds the matches to the block, and gives the model a `search_memory` tool.

`subject` is a fixed string or a function of the run context. Use a stable,
auth-derived identifier for the user, never a session or conversation id.

## Where the memory is stored

`Client(local_dir="./recall-data", typed=True)` keeps the database on this machine. `typed=True` asks for the typed tools this capability files beliefs with (recall 0.18 or later). The
first process to open the directory starts a `polign-server` for it in the
background, listening on localhost only, and every other process shares that
server. It keeps running after your program exits; its process id is in
`recall-data/runtime.json` and its log in `recall-data/server.log`. This is
the quickest way to try the package and is fine for a single machine.

Agents on several machines need one shared memory. Run `recall serve`
somewhere they can all reach, storing into S3, GCS or Azure
([Get started](https://polign.com/developers.html) shows how), then connect:

```python
recall = Client(url="https://memory.internal:8443", api_key=os.environ["RECALL_API_KEY"], typed=True)  # a recall serve started with -typed
```

`Client.from_env()` does the same from `RECALL_URL` and `RECALL_API_KEY`, and
starts a local process when `RECALL_URL` is unset, so the same code runs on a
laptop and in production.

Create one `Client` per process and share it between agents and runs. Each one
is a subprocess or an HTTP session, and calls through it are serialized; the
local read path takes a few milliseconds.

## Failures

Reads fail open. If Recall is slow or down when a run starts, the run goes on
without the remembered facts and a warning is logged on the
`recall_pydantic_ai` logger. Writes report the failure to the model as the
tool's result (`The fact was not saved: ...`), so it can tell the user. An
unknown predicate or a value of the wrong type asks the model to try again.

## Options

| Option | Default | What it does |
|---|---|---|
| `client` | required | A `polign_recall.Client` opened in typed mode (the default) |
| `subject` | required | The user, as a string or `lambda ctx: ...` |
| `limit` | 20 | Facts loaded into the instructions; above it, the run searches for the prompt and offers `search_memory` |
| `who` | `"the user"` | How the block and the tool descriptions refer to the person |
| `template` | `<recall_memory>\n{context}\n</recall_memory>` | Wrapper around the block; must contain `{context}` |
| `remember_tool` | on | Give the model the `remember` tool. Off makes the memory read-only |
| `forget_tool` | off | Give the model a `forget` tool, so users can withdraw a fact by asking |
| `search_when_overflowed` | on | Search for the prompt when the user has more than `limit` facts |
| `read_timeout`, `write_timeout` | 2 s, 10 s | Reads past the timeout fail open; writes past it tell the model the fact was not saved |

Inside a run, the capability instance the agent uses has `run_subject`,
`beliefs` (what was loaded) and `matches` (what the prompt search added).

## Custom predicates

The `remember` tool's schema is built from Recall's predicate registry, so the
model only ever sees the names you allow. The built-in registry covers names,
preferences, project facts and free-form notes. To use your own, write it as
JSON and point the server at it:

```json
{
  "name":       { "cardinality": "single", "value_type": "string",  "description": "The name the user asks to be called" },
  "plan_tier":  { "cardinality": "single", "value_type": "string",  "description": "The user's subscription plan" },
  "open_issue": { "cardinality": "multi",  "value_type": "string",  "description": "A problem the user reported that is not resolved yet" }
}
```

```python
recall = Client(local_dir="./recall-data", env={"POLIGN_PREDICATES": "registry.json"}, typed=True)
```

`single` means a newer value replaces the old one; `multi` means each value is
an additional fact. `value_type` is `string`, `number`, or `boolean`, and the
tool turns the model's text into that type ("1,200" becomes 1200, "yes"
becomes true). The file replaces the built-in registry, except `note`, which
Recall always keeps for statements no other predicate fits.

## Limits

- The client must use Recall's typed tools. A client opened with `text=True`
  is not supported yet.
- Durable execution (Temporal, DBOS) is not tested. The capability reads
  memory in `for_run`, so a worker that rebuilds the run reads it again.
- Agent specs (`Agent.from_spec`) cannot create `RecallMemory`, since it takes
  a live client.

## Development

```bash
cd python/recall-pydantic-ai
python -m pip install -e . pytest pytest-asyncio
pytest tests/unit_tests -q          # fake Recall client, scripted model
pytest tests/integration_tests -v   # the recall binary and polign-server pip installed
```

The integration tests start a local database in a temporary directory, stop
its server when they finish, and skip when the `recall` binary is not
installed. Supported versions: Python 3.10 and later, `pydantic-ai-slim` 2.16
and later in 2.x.
