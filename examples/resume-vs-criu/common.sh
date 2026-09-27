# Shared by the two demo scripts. Run them as root on a Linux host with
# Podman (runc as its runtime), CRIU, polign-server on 127.0.0.1:23000, the
# agent:v1 and agent:v2 images, and the OpenAI key in /dev/shm/openai.key.
set -euo pipefail
KEY=/dev/shm/openai.key
CRASH_AT=${CRASH_AT:-20}

title() { printf '\n\033[1;32m== %s\033[0m\n' "$*"; }
note()  { printf '\033[0;90m   %s\033[0m\n' "$*"; }

# calls NAME: how many model calls the container has logged.
calls() { podman logs "$1" 2>/dev/null | grep -c " call " || true; }

# wait_calls NAME N [FROM]: stream the container's calls after call FROM
# (default: the calls already shown) until it has made N.
wait_calls() {
  local seen=${3:-$(calls "$1")}
  while :; do
    local now; now=$(calls "$1")
    [ "$now" -gt "$2" ] && now=$2
    [ "$now" -gt "$seen" ] && podman logs "$1" 2>/dev/null | grep " call " | sed -n "$((seen + 1)),${now}p"
    seen=$now
    [ "$seen" -ge "$2" ] && return
    sleep 1
  done
}

run_agent() { # run_agent NAME IMAGE VOLUME RECALL
  podman run -d --name "$1" --network host -v "$3":/work -v "$KEY":/run/secrets/openai.key:ro \
    -e RECALL="$4" -e AGENT_ID=demo-migrator -e POLIGN_URL=http://127.0.0.1:23000 "$2" >/dev/null
}

# usage VOLUME: model calls and tokens across every process that worked on
# the task, from the ledger each agent appends to in /work.
usage() {
  podman run --rm -v "$1":/work docker.io/library/python:3.12-slim python -c "
import json
rows = [json.loads(l) for l in open('/work/ledger.jsonl')]
print('%d model calls, %s tokens in total' % (len(rows), format(sum(r['in'] + r['out'] for r in rows), ',')))"
}

# follow_until_done NAME SKIP: stream the container's call lines after the
# first SKIP until it reports DONE, stops, or exits.
follow_until_done() {
  local shown=$2
  while :; do
    local lines; lines=$(podman logs "$1" 2>/dev/null | grep -E " call |DONE|stopped with" || true)
    local total; total=$(printf '%s\n' "$lines" | grep -c . || true)
    [ "$total" -gt "$shown" ] && printf '%s\n' "$lines" | sed -n "$((shown + 1)),${total}p"
    shown=$total
    printf '%s\n' "$lines" | grep -qE "DONE|stopped with" && return
    [ "$(podman inspect "$1" --format '{{.State.Running}}' 2>/dev/null)" = true ] || return
    sleep 1
  done
}
