# LiveKit voice agent with Recall memory

A phone-support agent built on [LiveKit Agents](https://github.com/livekit/agents)
that remembers each caller across calls with
[Recall by Polign](https://polign.com/recall.html), through the
[`recall-livekit`](https://pypi.org/project/recall-livekit/) package.

What it does on a call:

1. Loads the caller's remembered facts before the greeting and greets them by
   name if it knows one.
2. Stores new facts the caller states through a `remember` tool. Facts are
   typed, so a new name replaces the old one and the history of the change is
   kept.
3. On the next call from the same identity, starts with everything in place.

## Run it

You need a LiveKit project, API keys for the model providers in `agent.py`,
the `polign` CLI on your `PATH`, and a polign_db server to store into.

```bash
# a local memory store
polign-server -store fs:/tmp/polign-callers

# the agent
pip install -r requirements.txt
cp .env.example .env         # fill it in
python agent.py console      # talk to it in the terminal
python agent.py dev          # connect it to your LiveKit project
```

In `console` mode every run uses the identity LiveKit assigns to the console
participant, so use `dev` with a real token to see memory carry across calls.

## Deploy it

`Dockerfile` builds a worker image with the `polign` CLI copied from the
published `ghcr.io/polign/polign-server` image. Set `POLIGN_URL` and
`POLIGN_API_KEY` on the container so the subprocess can reach your memory
store. LiveKit Cloud deploys from this Dockerfile as is.

## Your own predicates

Copy the starter registry and edit it, then point `POLIGN_PREDICATES` at it:

```bash
python -c "import recall_livekit, shutil; shutil.copy(recall_livekit.VOICE_REGISTRY, 'callers.json')"
```

Each entry has a `cardinality` (`single` replaces, `multi` accumulates), a
`value_type` (`string`, `number`, `boolean`) and a description the model reads.
The file replaces the built-in registry entirely.

## Knowledge base instead of memory

`knowledge/` is the retrieval side: `index.py` indexes a folder of documents
into a polign_db collection, and `knowledge/agent.py` looks up the closest
passages on every user turn with keyword search. Both patterns can live in one
agent.
