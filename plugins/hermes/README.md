# Recall for Hermes Agent

Long-term memory for [Hermes Agent](https://github.com/NousResearch/hermes-agent),
kept in [Recall by Polign](https://polign.com/recall). A new statement replaces
the old one, the old one stays in the history, and memory can be read as of
any earlier time. The agent sees the correction, not just the latest value.

What the plugin does once it is the active memory provider:

- Puts relevant memories in front of each turn, each with when it was stated
  and what it replaced.
- Sends each completed turn to Recall in the background, where an extraction
  model works out the facts. "I moved to Portland" retires "Lives in Seattle".
- Mirrors the built-in MEMORY.md and USER.md writes into Recall.
- Gives the agent five tools: `recall_search`, `recall_profile`,
  `recall_remember`, `recall_forget`, `recall_history`.

Memory lives in a local database under the Hermes profile by default, with
nothing to configure. It can also live in a `recall serve` you run, shared
with your other agents, or in a database you choose.

## Install

From the Hermes plugin catalog, once the entry is merged:

```bash
hermes plugins install recall
hermes memory setup          # pick recall
```

Until then, drop the directory into the profile and install its one dependency
into the Hermes environment:

```bash
git clone --depth 1 https://github.com/Polign/polign.git /tmp/polign
cp -r /tmp/polign/plugins/hermes ~/.hermes/plugins/recall
pip install "polign-recall>=0.25.0"      # in the Python environment Hermes runs in
hermes memory setup                      # pick recall
```

Or set it by hand in `~/.hermes/config.yaml`:

```yaml
memory:
  provider: recall
```

`hermes memory status` shows where memory lives and which extraction model is
in use.

The `polign-recall` package ships the `recall` binary. On first use with the
local database, `recall` downloads `polign-server` (about 80 MB) from
dl.polign.com and checks it against a SHA-256 built into the binary. Agent
startup does not wait for this; the first turns run without memory until the
database is up.

## The extraction model

Recall works out the facts in each turn with a model, so that a later
statement can replace an earlier one. Without a model, text is kept as
written and found by its words, and nothing replaces anything.

| Setting | Model | Key read from `.env` |
| --- | --- | --- |
| blank (default) | `anthropic:claude-haiku-5-5` if `ANTHROPIC_API_KEY` is set, else `openai:gpt-5.4-mini` if `OPENAI_API_KEY` is set, else none | that key |
| `anthropic:<model>` | the named Claude model | `ANTHROPIC_API_KEY` |
| `openai:<model>` | the named OpenAI model | `OPENAI_API_KEY` |
| `ollama:<model>` | a local Ollama model; nothing leaves the machine | none |
| `none` | no model; text kept as written | none |

Set it in `hermes memory setup`, as `extract_model` in `$HERMES_HOME/recall.json`,
or as `RECALL_EXTRACT_MODEL` in the profile's `.env`. The embedder follows the
model: Anthropic uses the model built into `recall`, OpenAI uses
`text-embedding-3-small`, Ollama uses `nomic-embed-text`. See the
[Recall reference](https://github.com/Polign/recall/blob/main/docs/reference.md).

## Where memory lives

In order, the first that is set wins:

| Setting | Memory lives in |
| --- | --- |
| `RECALL_URL` and `RECALL_API_KEY` in `.env`, or `url` in `recall.json` and the key in `.env` | a [`recall serve`](https://github.com/Polign/recall/blob/main/docs/memory-api-v1.md) you run, started with `-text`. The key picks the namespace; the extraction model is the server's. |
| `RECALL_BACKEND` and `RECALL_BACKEND_URL` (and `RECALL_BACKEND_KEY`) in `.env` | a database you chose: `local` with a directory, `polign` with `http://host:23000`, `pgvector` with `postgres://...`, `mongodb` or `qdrant` |
| nothing | a local database in `$HERMES_HOME/recall`, run by `recall` itself |

### The local database is a separate process

In the default case, `recall` starts a `polign-server` process for
`$HERMES_HOME/recall` on a loopback port, with a key generated in that
directory. The server is detached, so it keeps running after Hermes exits,
and the next Hermes session finds it again. Its process id is in
`$HERMES_HOME/recall/runtime.json` and its log in `server.log` in the same
directory. To stop it:

```bash
kill "$(python3 -c 'import json,os;print(json.load(open(os.path.expanduser("~/.hermes/recall/runtime.json")))["pid"])')"
```

(replace `~/.hermes` with your `HERMES_HOME` if it is elsewhere). Memory is
on disk under that directory, so stopping the server loses nothing; the next
session starts it again.

### What the recall process sees

The plugin starts the `recall` process with only `PATH`, the home and
temporary directories, the `RECALL_*` and `POLIGN_*` variables, and the one
extraction key named by the model in use. The other provider keys and bot
tokens in Hermes's `.env` do not reach it.

A Hermes profile has its own memory in the default case, since each profile
has its own `HERMES_HOME`. Point several profiles, or several agents, at one
`recall serve` to share memory between them.

## Tools

| Tool | What it does |
| --- | --- |
| `recall_search(question, as_of?)` | Memories relevant to a question, each with the date stated and what it replaced. `as_of` answers as of an earlier time. |
| `recall_profile()` | Everything currently believed about the user, newest first. |
| `recall_remember(text, observed_at?)` | Store a statement. The result says what it replaced. |
| `recall_forget(text)` | Withdraw what a description names. Needs an extraction model. Nothing is deleted. |
| `recall_history(question)` | How what is believed changed, statement by statement, including things since forgotten. |

Before each turn the plugin injects a `## Recall Memory` block with up to eight
memories, found by meaning for the turn's message. The recall starts when the
turn starts and the turn waits at most three seconds for it; a slow server
means the turn goes on without the block and the tools remain.

## Limits

- Text mode has one subject, the user. On a gateway serving many people from
  one profile, their memories are not separated. Give each person a profile,
  or a key of their own on a `recall serve`.
- `recall_forget` and the mirroring of MEMORY.md removals need an extraction
  model; without one they are skipped.
- A `recall serve` must serve the text tools (`-text`). The typed tools are not
  used by this plugin.
- Turns longer than 4,000 characters per side are cut at the last sentence end
  before being sent for extraction.

## Development

```bash
cd plugins/hermes
python -m venv .venv && . .venv/bin/activate
pip install -e '.[dev]'
pytest tests -q               # unit tests over a fake client, then the real recall binary
```

The unit tests stub the Hermes modules the plugin imports
(`agent.memory_provider`, `agent.secret_scope`, `tools.registry`,
`hermes_constants`), so they run without a Hermes checkout. The integration
test starts a local database under a temporary profile.
