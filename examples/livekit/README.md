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

You need a LiveKit project and API keys for the model providers in `agent.py`.
The memory store needs no setup: pip installs the database with the package,
and the agent keeps its data in `./recall-data`.

```bash
pip install -r requirements.txt
cp .env.example .env         # fill it in
python agent.py console      # talk to it in the terminal
python agent.py dev          # connect it to your LiveKit project
```

In `console` mode every run uses the identity LiveKit assigns to the console
participant, so use `dev` with a real token to see memory carry across calls.

## Deploy it

`Dockerfile` builds a worker image with nothing but `pip install`. A
container's disk does not survive a redeploy, so a deployed worker should
store into a polign_db server instead of `./recall-data`: set `POLIGN_URL` and
`POLIGN_API_KEY` on the container and the agent uses that server.
[Get started](https://polign.com/developers.html) shows how to run one.
LiveKit Cloud deploys from this Dockerfile as is.

## Use an S3-backed store

The connection is `LiveKit worker → polign mcp → polign-server → S3`.
`POLIGN_URL` is the server's HTTP(S) address. Configure the bucket on the
server; AWS credentials belong on that server, and `local_dir` is unused
when the example's `POLIGN_URL` is set.

On the machine hosting the memory server, install the binaries and create a
private API-key file once (keep the same file across restarts):

```bash
python -m venv .venv
source .venv/bin/activate
pip install 'polign_db>=0.7.0'
python - <<'PY'
import os, secrets
with os.fdopen(os.open('memory-api-key', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as f:
    f.write(f'plgn_{secrets.token_hex(8)}_{secrets.token_hex(32)}\n')
PY
```

Use an existing bucket and a dedicated prefix. The server's AWS identity
needs `s3:ListBucket` on the bucket, and `s3:GetObject`, `s3:PutObject`, and
`s3:DeleteObject` on the prefix, plus access required by the bucket's
encryption policy. For a local server, select your AWS profile and region;
on AWS, use the workload's IAM role instead of `AWS_PROFILE`.

```bash
export AWS_PROFILE=your-profile
export AWS_REGION=us-east-1
polign-server \
  -store s3://your-bucket/recall-livekit \
  -require-data-key -bootstrap-key-file ./memory-api-key \
  -http 127.0.0.1:23000 -grpc 127.0.0.1:23001 \
  -telemetry=false
```

For workers on other machines, bind HTTP to the server's private interface
and make it reachable from the worker; use HTTPS when crossing public networks.
The released binaries include S3 support; builds from source need `-tags cloud`.

In the LiveKit worker's `.env`, set:

```dotenv
POLIGN_URL=http://127.0.0.1:23000
POLIGN_API_KEY=<contents of memory-api-key>
POLIGN_COLLECTION=callers
```

Use the reachable server address in place of localhost for a remote worker.
Keep the key in `.env` or your deployment's secret manager.

Before starting a voice session, verify memory by itself:

```bash
python verify_memory.py
```

This writes and corrects a synthetic name in `callers_setup_check`, then reads
it through a fresh Recall subprocess. It prints a `--read-only` command: stop
and restart the memory server with the same S3 store and key, then run that
command to verify recovery. The synthetic facts remain in the test collection.
This check needs only `recall-livekit` and `python-dotenv`, with no LiveKit or
model-provider credentials. Finally run `python agent.py dev` for a voice call.

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
