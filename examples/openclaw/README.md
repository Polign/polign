# OpenClaw with Recall memory, on one machine

Two scripts that run the memory side of
[Recall for OpenClaw](../../plugins/openclaw) on your laptop: polign_db,
`recall serve`, and a Recall key, with OpenClaw pointed at them. Everything
stays local; with the default model nothing leaves the machine.

## Before you start

- `recall` 0.16.0 or later, which brings `polign-server`:
  `brew install polign/tap/recall` or `pip install polign-recall`.
- An extraction model. The default is `ollama:qwen3:8b`, so install
  [Ollama](https://ollama.com) and run `ollama pull qwen3:8b`. For better
  extraction set `RECALL_EXTRACT_MODEL=anthropic:claude-haiku-4-5-20251001`
  and `ANTHROPIC_API_KEY`.
- OpenClaw 2026.10.2 or later: `npm install -g openclaw`. Optional for the
  first run; the script configures it when it finds it.

## Start

```sh
./up.sh
```

It starts polign_db on `127.0.0.1:23000` and `recall serve` on
`127.0.0.1:8080`, mints a Recall key for the `openclaw` namespace once, and,
when `openclaw` is on your PATH, installs the plugin, sets the memory slot,
allows the hooks, and turns off the built-in memory-core. Run it again at any
time; it reuses what is already up. Then restart the OpenClaw gateway and
check:

```sh
openclaw plugins inspect recall --runtime
openclaw recall status
```

`./down.sh` stops both servers. Memories, logs and the key stay under
`~/.local/share/polign/openclaw`.

## Try a correction

Talk to the agent through the gateway, not `openclaw agent --local`: captures
run in the background after each turn, and a one-shot process exits first.

```text
You:   Quick fact about me: I drive a Honda Civic.
You:   Update: I sold the Civic. I drive a Tesla Model 3 now.
       (new session)
You:   What car do I drive?
Agent: You drive a Tesla Model 3.
```

Recall now holds the Tesla as current and the Civic as what it replaced:

```sh
openclaw recall search "what car does the user drive?"
```

## Settings

| Variable | Default | Meaning |
| --- | --- | --- |
| `RECALL_EXTRACT_MODEL` | `ollama:qwen3:8b` | The model that works facts out of text, as `provider:model`. |
| `RECALL_OPENCLAW_DIR` | `~/.local/share/polign/openclaw` | Data, logs, pid files and the key. |
| `POLIGN_HTTP`, `POLIGN_GRPC` | `127.0.0.1:23000`, `127.0.0.1:23001` | polign_db addresses. |
| `RECALL_LISTEN` | `127.0.0.1:8080` | Where `recall serve` listens. |

## What to expect from a local model

qwen3:8b takes about 13 seconds to work out a short exchange on an Apple
silicon laptop, which is why captures run in the background. It handles plain
statements ("I live in Bellevue now") and misses some indirect ones ("I moved
to Bellevue last week"). A hosted model catches more.
