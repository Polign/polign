# Recall for OpenClaw

Long-term memory for [OpenClaw](https://openclaw.ai) agents, kept in
[Recall by Polign](https://github.com/Polign/recall). The plugin fills
OpenClaw's memory slot: before each turn it puts what Recall remembers in front
of the prompt, after each turn it sends the conversation to Recall, and the
agent gets three tools to search, store and forget on its own.

What Recall adds over a search index:

- **A correction replaces.** "I moved to Bellevue" replaces "I live in
  Seattle". The agent sees the current value and what it replaced, so it can
  mention the change when that matters, and it never sees both as equals.
- **Nothing is deleted.** Every statement stays in the history. `memory_search`
  takes `as_of`, so the agent can ask what was believed last month.
- **Your server, your bucket.** Memories go over HTTP to `recall serve`, which
  keeps them in polign_db on local disk or in your own object store bucket.
  The Recall key chooses the namespace, so one server can hold memory for many
  agents without any of them reaching another's records.

## Before you start

- OpenClaw 2026.10.2 or later on Node 24.16 or later.
- `recall` 0.16.0 or later: `brew install polign/tap/recall` or
  `pip install polign-recall`. Either way you also get `polign-server`.
- A model for working facts out of text, as `provider:model`: a hosted one
  such as `anthropic:claude-haiku-4-5-20251001` with its key in the
  environment, or a local one such as `ollama:qwen3:8b`. Without one, Recall
  keeps captured text as written: searchable, but nothing replaces anything,
  and `memory_forget` is refused. A small local model misses some phrasings;
  in testing, qwen3:8b caught "I live in Bellevue now" but not "I moved to
  Bellevue last week".

## Run the memory server

Start polign_db and `recall serve` in text mode, then mint a key for OpenClaw.
The server holds the database; OpenClaw holds only its key.

```sh
polign-server -store fs:$HOME/.local/share/polign/openclaw
recall -url http://127.0.0.1:23000 serve -text -listen 127.0.0.1:8080 \
  -extract-model anthropic:claude-haiku-4-5-20251001
recall -url http://127.0.0.1:23000 key create -namespace openclaw -expires 8760h
```

The last command prints a key that starts with `rcl_`, once. That is the only
credential the plugin needs; the namespace it names is where the memories
live. Both servers listen on loopback by default. `-store` takes an S3, GCS or
Azure URL as well as a local path, and a database that requires keys takes its
key in `POLIGN_API_KEY`; see [Run polign_db](https://polign.com/developers).

## Install the plugin

```sh
openclaw plugins install clawhub:@polign/openclaw-recall
```

It is listed on ClawHub as Recall by Polign, so `openclaw plugins search
polign` finds it too. The same package is on npm as `@polign/openclaw-recall`;
installing from there asks you to confirm, and a script needs
`--force --accept-capabilities`. The [local example](../../examples/openclaw)
does all of the steps below for you.

Then in `openclaw.json`:

```json
{
  "plugins": {
    "slots": { "memory": "recall" },
    "entries": {
      "recall": {
        "enabled": true,
        "hooks": { "allowConversationAccess": true },
        "config": {
          "url": "http://127.0.0.1:8080",
          "apiKey": "${RECALL_API_KEY}"
        }
      }
    }
  }
}
```

`slots.memory` makes Recall the memory engine instead of the built-in one.
`hooks.allowConversationAccess` lets the plugin read the prompt and the turn's
messages, which auto-recall and auto-capture need; OpenClaw asks this of every
plugin it did not ship. Put the key in `RECALL_API_KEY` or write it in place of
`${RECALL_API_KEY}`.

Then turn off the built-in memory plugin. OpenClaw 2026.10 keeps it loaded
even when the slot points elsewhere, and it registers `memory_search` first,
so without this step the agent's searches go to the workspace Markdown files
instead of Recall:

```sh
openclaw plugins disable memory-core
```

Restart the gateway, then:

```sh
openclaw recall status
openclaw recall remember "I moved to Bellevue and I prefer concise answers"
openclaw recall search "where does the user live?"
```

To try the plugin from a checkout instead of npm, build it and point OpenClaw
at the folder:

```sh
cd /path/to/polign/plugins/openclaw && npm install && npm run build
openclaw plugins install --link --force --accept-capabilities "$PWD"
openclaw plugins inspect recall --runtime
```

## What the agent gets

| Tool | What it does |
| --- | --- |
| `memory_search` | Asks what is remembered, in words. Each answer shows when it was stated and what it replaced. `as_of` answers as of an earlier time. |
| `memory_store` | Keeps something worth remembering: a preference, a fact, a decision, a correction. Recall works out the statements; a newer one replaces an older one about the same thing, and restating something known changes nothing. |
| `memory_forget` | Stops believing what a description names, such as "my editor". Nothing is deleted, and remembering it again brings it back. |

And two hooks, each on by default:

- **Auto-recall** (`before_prompt_build`): asks Recall about the user's
  message and puts up to `maxMemories` answers in front of the prompt, inside
  a `<relevant-memories>` block. Nothing is added when Recall has nothing to
  say or does not answer.
- **Auto-capture** (`agent_end`): after a successful turn, sends the latest
  `captureMessages` user and assistant messages to Recall as one passage.
  Injected memory blocks are stripped first, so memory never feeds itself.
  The capture runs in the background, one at a time, because extraction can
  take longer than the 30 seconds OpenClaw waits for a hook. It needs a
  process that stays up, such as the gateway: a one-shot
  `openclaw agent --local` exits before the capture finishes.

## Settings

All under `plugins.entries.recall.config`. The environment is the fallback
for the first two.

| Setting | Default | Meaning |
| --- | --- | --- |
| `url` | `$RECALL_URL`, then `http://127.0.0.1:8080` | Where `recall serve` listens. |
| `apiKey` | `$RECALL_API_KEY` | A Recall key, `rcl_...`. It chooses the namespace. |
| `autoRecall` | `true` | Put relevant memories in front of each prompt. |
| `autoCapture` | `true` | Send the recent conversation to Recall after each turn. |
| `maxMemories` | `5` | How many memories auto-recall injects. |
| `captureMessages` | `10` | How many of the latest messages auto-capture sends. |
| `timeoutMs` | `60000` | How long the agent's memory tools wait. Storing waits for the extraction model. Auto-recall waits at most 10 seconds, inside OpenClaw's 15 second hook limit. |
| `captureTimeoutMs` | `120000` | How long one background capture waits. |

## What works differently from the built-in memory

- Memories are statements Recall worked out, not Markdown files in the
  workspace. `MEMORY.md` and the daily notes are not read or written.
- There is no list-everything tool. `openclaw recall search` with a broad
  question, or `recall explain` against the server, show what is held.
- Recall needs its server running. When it is down, the hooks log a warning
  and add nothing, and the tools report the error to the agent.

## Develop

```sh
npm install
npm test          # node --test, no network
npm run check     # tsc against the OpenClaw plugin SDK
npm run build     # dist/, the compiled entry OpenClaw loads from npm
```

From a checkout OpenClaw loads the TypeScript source directly; from npm it
loads `dist/index.js`, which `npm publish` builds first. `openclaw plugins
validate` applies to plugins written with `defineToolPlugin`, not to this one;
use `openclaw plugins inspect recall --runtime` to see what it registered.

## Source and license

Apache License 2.0, in
[github.com/Polign/polign/plugins/openclaw](https://github.com/Polign/polign/tree/main/plugins/openclaw).
Recall itself is at [github.com/Polign/recall](https://github.com/Polign/recall),
and the HTTP contract the plugin is written against is its
`docs/memory-api-v1.md`.
