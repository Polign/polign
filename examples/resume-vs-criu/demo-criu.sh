#!/usr/bin/env bash
# The agent protected by a container snapshot (CRIU), crashed, then upgraded.
source "$(dirname "$0")/common.sh"
CKPT=/var/tmp/agent-checkpoint.tar.gz
CKPT_AT=${CKPT_AT:-12}
podman rm -af >/dev/null 2>&1 || true; podman volume rm -f crwork >/dev/null 2>&1 || true; rm -f "$CKPT"

title "agent:v1 (gpt-4.1-mini) starts, state in process memory only"
# --dns=none with a resolv.conf the container writes itself: Podman does not
# restore the resolv.conf it manages, and without DNS a restored agent cannot
# reach the model API again. A team relying on CRIU would need this too.
podman run -d --name agent --network host --dns=none -v crwork:/work -v "$KEY":/run/secrets/openai.key:ro \
  -e RECALL=off --entrypoint sh agent:v1 -c "echo nameserver ${DNS:-169.254.169.254} > /etc/resolv.conf; exec python agent.py" >/dev/null
wait_calls agent "$CKPT_AT"
shown=$CKPT_AT

title "Snapshot it mid-task (podman container checkpoint)"
if podman container checkpoint --leave-running --export "$CKPT" agent >/dev/null 2>&1; then
  note "checkpoint taken"
else
  id=$(podman inspect agent --format '{{.Id}}')
  note "FAILED. CRIU says:"
  grep -h "Connected TCP socket" /var/lib/containers/storage/overlay-containers/"$id"/userdata/dump.log | sed 's/^.*Error/   Error/' || true
  note "the agent holds an open connection to the model API"
  title "Force it with --tcp-established"
  podman container checkpoint --leave-running --tcp-established --export "$CKPT" agent >/dev/null
fi
note "checkpoint: $(du -h "$CKPT" | cut -f1) of process memory (the API key is in it)"

title "The agent keeps working, then crashes at call $CRASH_AT"
wait_calls agent "$CRASH_AT" "$shown"
podman kill -s KILL agent >/dev/null; podman rm -f agent >/dev/null
note "killed with SIGKILL: $((CRASH_AT - CKPT_AT)) calls since the snapshot are lost"

title "Upgrade: agent:v2 answers with gpt-4.1. Restore the snapshot"
podman container restore --import "$CKPT" --tcp-established --ignore-volumes >/dev/null
note "restored image: $(podman inspect agent --format '{{.ImageName}}') (a snapshot can only bring back what it froze)"
note "it continues from its state at call $CKPT_AT, redoing the calls since:"
follow_until_done agent "$CKPT_AT"
note "finished as agent:v1 on gpt-4.1-mini: the upgrade did not happen"
note "$(usage crwork)"
rm -f "$CKPT"
