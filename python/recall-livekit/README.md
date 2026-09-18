# recall-livekit

Long-term memory for [LiveKit Agents](https://github.com/livekit/agents) voice
agents, backed by [Recall by Polign](https://polign.com/recall.html).

The agent loads everything it knows about the caller before the first reply,
and the model saves new facts through a `remember` tool. Facts are typed:
each one is a predicate from a closed registry with a value, so "call me Sam"
replaces the old name instead of piling up a second one, and the history of
the change is kept. No second model runs on the read path, and no embedding
service is needed.

```bash
pip install recall-livekit "livekit-agents[openai,deepgram,cartesia,silero]"
```

Recall runs as a `polign mcp` subprocess, so the `polign` CLI has to be on the
worker's `PATH` (or passed as `command=`), and it needs a polign_db server to
store into. [Get started](https://polign.com/developers.html) covers both.

## Usage

```python
from livekit.agents import AgentServer, AgentSession, JobContext, JobProcess, cli
from livekit.plugins import cartesia, deepgram, openai, silero
from recall_livekit import VOICE_REGISTRY, RecallAgent, RecallMemory

server = AgentServer()


def setup(proc: JobProcess) -> None:
    # one Recall subprocess per worker process
    proc.userdata["recall"] = RecallMemory.open(
        url="http://memory.internal:23000",   # or POLIGN_URL in the environment
        predicates=VOICE_REGISTRY,            # or your own registry file
    )


server.setup_fnc = setup


@server.rtc_session()
async def entrypoint(ctx: JobContext) -> None:
    participant = await ctx.wait_for_participant()
    memory = ctx.proc.userdata["recall"].for_subject(participant.identity)

    session = AgentSession(
        stt=deepgram.STT(), llm=openai.LLM(model="gpt-4.1-mini"),
        tts=cartesia.TTS(), vad=silero.VAD.load(),
    )
    await session.start(
        agent=RecallAgent(
            memory=memory,
            instructions="You are the support line for Acme. Use what you remember about the caller.",
        ),
        room=ctx.room,
    )


if __name__ == "__main__":
    cli.run_app(server)
```

What happens on a call:

1. `on_enter` loads the caller's current beliefs and appends them to the
   instructions inside a `<recall_memory>` block, before the greeting.
2. The caller says "actually call me Sam, and I moved to Denver". The model
   calls `remember` twice. Recall supersedes the old name and timezone, and
   the agent rewrites its instructions so the next sentence already uses Sam.
3. A week later the same identity calls back and step 1 finds the facts.

Nothing is searched per turn. A caller's typed facts are a short list, so the
whole set fits in the prompt. When a caller has more beliefs than `limit`
(default 20), each user turn also runs a search and adds matching facts to
that turn only.

## Your own Agent subclass

```python
from recall_livekit import attach

agent = FrontDesk()                      # any Agent with string instructions
await attach(agent, memory, who="the guest")
await session.start(agent, room=ctx.room)
```

`attach` adds the memory block and the `remember` tool. It does not do the
per-turn search for overflowing callers; use `RecallAgent` for that.

## Options

| Where | Option | Default | What it does |
|---|---|---|---|
| `RecallMemory.open` | `url`, `api_key`, `collection`, `predicates` | worker environment | Connection for the subprocess (`POLIGN_URL`, `POLIGN_API_KEY`, `POLIGN_COLLECTION`, `POLIGN_PREDICATES`) |
| `RecallMemory.open` | `command` | `polign mcp -memory-only -write` | The subprocess argv, for a binary that is not on `PATH` |
| `for_subject` | `limit` | 20 | Beliefs loaded into the prompt; above it, per-turn search kicks in |
| `for_subject` | `read_timeout`, `write_timeout` | 0.5 s, 5 s | Reads fail open (last known beliefs); writes tell the model the fact was not saved |
| `RecallAgent` | `context_template` | `<recall_memory>\n{context}\n</recall_memory>` | Wrapper around the block; must contain `{context}` |
| `RecallAgent` | `who` | `"the caller"` | How the block refers to the person |
| `RecallAgent` | `remember_tool`, `forget_tool` | on, off | Which tools the model gets |
| `RecallAgent` | `search_when_overflowed` | on | Per-turn search when the caller has more beliefs than `limit` |

## Custom predicates

Predicates are a JSON file. The `remember` tool's schema is built from it, so
the model only ever sees the names you allow.

```json
{
  "name":       { "cardinality": "single", "value_type": "string",  "description": "The name the caller asks to be called" },
  "open_issue": { "cardinality": "multi",  "value_type": "string",  "description": "A problem the caller reported that is not resolved yet" }
}
```

`single` means a newer value replaces the old one; `multi` means each value
is an additional fact. `value_type` is `string`, `number`, or `boolean`. The
file replaces Recall's built-in registry entirely. `VOICE_REGISTRY` is a
starter set for callers: name, preferred language, callback number, email,
timezone, account tier, recording consent, response style, open issues,
owned products, and technologies used.

## Subjects and retention

Use a stable, auth-derived identifier as the subject, such as the participant
identity your token server issued. Never the room name. Recall keeps the
record of every change while the current view updates; enable `forget_tool`
if callers should be able to withdraw a fact by asking, and see the Recall
docs for retention and export.

## Development

`polign-recall` is not on PyPI yet, so install it from source first:

```bash
python -m pip install "polign-recall @ git+https://github.com/Polign/recall@python-v0.2.0#subdirectory=python"
cd python/recall-livekit
python -m pip install -e . pytest pytest-asyncio
pytest tests/unit_tests -q                # fake Recall subprocess, scripted model
pytest tests/integration_tests -v         # real polign-server and polign CLI
```

The integration tests locate the binaries like the SDK's tests do
(`POLIGN_SERVER`, `POLIGN_SOURCE`, `POLIGN_SERVER_VERSION`, or the latest
release download) and skip when none is found.
