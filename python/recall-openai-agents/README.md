# recall-openai-agents

Session memory for the [OpenAI Agents SDK](https://openai.github.io/openai-agents-python/),
backed by [Recall by Polign](https://polign.com/recall.html).

The Agents SDK keeps a conversation going across runs through a `Session`:
the runner reads the history from it before each run and writes the new
items back after. `RecallSession` is that session over Recall, so the
history lives in the same database as the agent's memory and state, is read
by any process, and is written under a lease so two processes never
interleave writes to one session.

```bash
pip install recall-openai-agents
```

pip also brings the `recall` binary and `polign-server` for Linux, macOS
and Windows, so there is no separate database to download.

## Usage

```python
from agents import Agent, Runner
from recall_openai_agents import RecallSession

agent = Agent(name="assistant", instructions="Reply briefly.")
session = RecallSession("support-42", local_dir="./recall-data")

result = await Runner.run(agent, "Hi, I am Sam.", session=session)
result = await Runner.run(agent, "What is my name?", session=session)
print(result.final_output)  # Sam
```

Run it twice, or from two processes, and the second run continues the
first: the session id is what names the conversation.

What happens:

1. Before each run the SDK calls `get_items`, which reads the session's
   record and hands the history back oldest first. `get_items(limit=n)`
   returns the latest `n`.
2. After each run the SDK calls `add_items` with the run's new items (the
   input, the model's messages, tool calls and their outputs). They are
   appended to the record under the session's lease, which is taken for the
   write and handed back after it.
3. Each message, tool call and tool output is also recorded as a turn of a
   Recall agent named `oa-<session_id>`. `agent_resume` on that id, from the
   Recall MCP tools or `polign_recall.Client.resume`, briefs another process
   with the conversation; the record stays as it was said, so `pop_item` and
   `clear_session` change what the SDK reads back, not that record. Pass
   `record_turns=False` to keep the history only.
4. `pop_item` removes and returns the latest item, and `clear_session`
   empties the history, as the SDK expects.

`RecallSession` implements the SDK's `Session` protocol, so anything that
takes a session takes it: `Runner.run`, `run_sync` and `run_streamed`,
`SessionSettings(limit=...)`, and `session_input_callback`.

## Options

| Option | Default | What it does |
|---|---|---|
| `client` | none | A `polign_recall.Client` opened with `agent=True`, to share one subprocess. Without it, `RecallSession` opens its own and closes it on `close()`. |
| `backend`, `backend_url`, `backend_key` | what the `recall` binary resolves: the environment, the saved setup, or a local database it runs itself | Where the records live, the same three names as `recall -backend`, `-url` and `-key` (only when `RecallSession` opens its own client) |
| `local_dir` | none | Keep the database in this directory and run its server; the short spelling of `backend="local", backend_url=...` |
| `env` | process environment | Extra environment for the subprocess, such as `RECALL_COLLECTION` |
| `record_turns` | `True` | Record each item as a turn of the Recall agent `oa-<session_id>` as well |
| `lease_ttl` | 10 | Seconds a write's lease lasts if the process dies in the middle of it |
| `lease_wait` | 30 | Seconds a write waits for another process to finish with the session before failing with `RecallError` code `"lease_held"` |

Session ids may hold any character; an id Recall cannot use as an agent name
as is (anything outside letters, digits, `.`, `_`, `-`, or longer than 125
characters) is hashed.

## Limits

The history of one session is one Recall record, bounded by Recall's output
size of 1 MiB. That is a few thousand ordinary items; a session that grows
past it fails to write, so clear or compact sessions that run that long
(`SessionSettings(limit=...)` bounds what the model sees, not what is
stored).

The `recall` binary resolves the database the way the `recall` command line
does; a `RecallSession` opened without `client` and without a database named
uses the local database every agent on the machine shares, under
`~/.local/share/recall`.

## Development

```bash
cd python/recall-openai-agents
python -m pip install -e . pytest pytest-asyncio
pytest tests/unit_tests -q                # a fake Recall client, real Runner
pytest tests/integration_tests -v         # the real recall binary and polign-server
```

Both suites drive real `Runner.run` calls with the SDK's `ScriptedModel`, so
no model API key is needed. Tested against openai-agents 0.24.0.
