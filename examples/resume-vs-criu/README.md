# Resume versus snapshot: crash an agent, upgrade it, bring it back

The same coding agent, crashed mid-task and then upgraded to a new image and
a newer model, recovered two ways:

- **Container snapshot (CRIU):** the whole process is checkpointed and
  restored.
- **Polign Recall:** the agent records its turns and notes as it works, and
  the new container resumes from them with
  [recall-langgraph](../../python/recall-langgraph/README.md).

The agent migrates `billing.charge(x)` calls to
`billing.charge_v2(x, currency="USD")` across a 21-file repository, one tool
call per turn, the task from the
[resume gate](../../python/recall-langgraph/bench/README.md). `agent:v1`
answers with gpt-4.1-mini; `agent:v2`, the upgrade, with gpt-4.1. Every model
call prints the image version and the model that answered.

## What happened

Recorded on September 26, 2026, with Podman 4.9 (runc runtime), CRIU 4.2.1 and
polign_db 0.8.1 on Ubuntu 24.04 (a GCP e2-standard-4), Recall storing to a
GCS bucket. The snapshot side gets its best case: both agents finish.

| | Container snapshot (CRIU) | Polign Recall |
|---|---|---|
| Save mid-task | The default checkpoint fails: `Connected TCP socket, consider using --tcp-established option`. The agent holds an open connection to the model API. | Nothing to take; turns and notes are recorded as it works |
| Forced | `--tcp-established` gives a 15 MB checkpoint of process memory, the API key included | 134 KB in the bucket for everything Recall kept |
| Crash at call 20, then upgrade | Restores as `agent:v1` on gpt-4.1-mini: a snapshot brings back only what it froze | `agent:v2` on gpt-4.1 waits out the dead container's lease, then resumes from 43 recorded turns |
| Work redone | The 8 model calls since the snapshot | None: it continues at the next file |
| All processes | 45 model calls, 93k tokens | 40 model calls, 96k tokens |
| Result | `run_tests` PASS, still as `agent:v1` on gpt-4.1-mini | `run_tests` PASS on gpt-4.1 |

Recall redid nothing, but every call after the restart carries the briefing,
so token use comes out about even. The difference is what each side ends up
with: the old agent from a 15 MB copy of its memory, or the upgraded agent
from 134 KB of records you can read.

What the snapshot side needed beyond a plain setup:

- **runc.** Ubuntu's default runtime, crun, cannot checkpoint.
- **`--tcp-established`.** Any agent that talks to a model API holds a
  connection open, so the default checkpoint fails.
- **DNS the container manages itself.** Podman does not restore the resolver
  file it manages, so a restored container has no DNS. `demo-criu.sh` runs
  the agent with `--dns=none` and writes `/etc/resolv.conf` inside the
  container (set `DNS` for a resolver other than GCP's 169.254.169.254).
  Without this, the restored agent cannot reach the model API again and
  exits with a connection error.

The recordings are `criu.cast` and `recall.cast` (play them with
`asciinema play criu.cast`), with `criu.gif` and `recall.gif` rendered from
them.

## Running it

You need a Linux host with root, Podman using runc (Ubuntu's default crun
cannot checkpoint), CRIU, and an OpenAI key.

```bash
sudo add-apt-repository -y ppa:criu/ppa
sudo apt-get install -y podman runc criu
printf '[engine]\nruntime = "runc"\n' | sudo tee /etc/containers/containers.conf

python3 -m venv ~/pv && ~/pv/bin/pip install polign_db==0.8.1
$(~/pv/bin/python -c 'import polign_db; print(polign_db.find_bin("polign-server"))') \
  -store fs:/var/lib/polign-demo -http 127.0.0.1:23000 &

sudo podman build -t agent:v1 .
sudo podman build -t agent:v2 -f Dockerfile.v2 .

# The key goes in RAM and is mounted read-only, never into an image.
umask 077; printf '%s' "$OPENAI_API_KEY" | sudo tee /dev/shm/openai.key >/dev/null

sudo bash demo-criu.sh
sudo bash demo-recall.sh      # STORE=gs://bucket/prefix also prints its size
# On a host outside GCP, set DNS to a resolver it can reach, e.g. DNS=1.1.1.1.
```

Each script takes about three minutes and a few cents of model calls.
`CRASH_AT` (default 20) sets the model call at which the agent is killed;
`CKPT_AT` (default 12) sets when `demo-criu.sh` snapshots it.

A CRIU checkpoint is a copy of the process's memory, so it holds the API key.
`demo-criu.sh` deletes the checkpoint when it finishes.
