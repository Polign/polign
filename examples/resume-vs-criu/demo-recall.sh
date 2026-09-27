#!/usr/bin/env bash
# The same agent recording to Polign Recall, crashed, then upgraded.
source "$(dirname "$0")/common.sh"
# STORE: the gs:// location polign-server was started with, to show its size.
STORE=${STORE:-}
podman rm -f recall-v1 recall-v2 >/dev/null 2>&1 || true; podman volume rm -f rcwork >/dev/null 2>&1 || true
AGENT_ID_SUFFIX=$(date +%s); export AGENT_ID=demo-migrator-$AGENT_ID_SUFFIX

title "agent:v1 (gpt-4.1-mini) starts, recording its turns to Polign Recall"
podman run -d --name recall-v1 --network host -v rcwork:/work -v "$KEY":/run/secrets/openai.key:ro \
  -e RECALL=on -e AGENT_ID="$AGENT_ID" -e POLIGN_URL=http://127.0.0.1:23000 agent:v1 >/dev/null
wait_calls recall-v1 "$CRASH_AT"

title "Crash at call $CRASH_AT"
podman kill -s KILL recall-v1 >/dev/null; podman rm -f recall-v1 >/dev/null
note "killed with SIGKILL: nothing released, nothing flushed"

title "Upgrade: start agent:v2 (gpt-4.1) with the same agent id"
podman run -d --name recall-v2 --network host -v rcwork:/work -v "$KEY":/run/secrets/openai.key:ro \
  -e RECALL=on -e AGENT_ID="$AGENT_ID" -e POLIGN_URL=http://127.0.0.1:23000 agent:v2 >/dev/null
until podman logs recall-v2 2>/dev/null | grep -qE "DONE|stopped with|Error:"; do sleep 1; done
podman logs recall-v2 2>/dev/null | grep -E "resumed|lease| call |DONE|stopped with" | sed 's/^/   /'
note "$(usage rcwork)"
if [ -n "$STORE" ]; then
  note "everything Recall keeps, in the bucket: $(gcloud storage du -s "$STORE" 2>/dev/null | awk '{printf "%.0f KB", $1/1024}')"
fi
