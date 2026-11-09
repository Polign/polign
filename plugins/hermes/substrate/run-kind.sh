#!/usr/bin/env bash
# The Recall memory provider for Hermes inside an Agent Substrate actor.
#
# Needs: Recall on Substrate already running on kind, from
# deploy/agent-substrate/run-kind.sh in github.com/Polign/recall; a
# hermes-agent checkout named by HERMES_AGENT_DIR; docker, kubectl, curl, and
# kubectl-ate on PATH or named by ATE.
#
#   HERMES_AGENT_DIR=~/code/hermes-agent ./run-kind.sh
#   ./run-kind.sh clean
set -o errexit -o nounset -o pipefail

cd "$(dirname "$0")"
CONTEXT="${CONTEXT:-kind-kind}"
REGISTRY="${REGISTRY:-localhost:5001}"
ATE="${ATE:-kubectl-ate}"
ATESPACE=recall-demo
ACTOR=hermes-1
PORT=8111

k() { kubectl --context "$CONTEXT" "$@"; }
ERR="$(mktemp)"
ate() {
  local rc=0
  "$ATE" --context "$CONTEXT" "$@" 2>"$ERR" || rc=$?
  grep -v '^Warning: no --token-file' "$ERR" >&2 || true
  return "$rc"
}
say() { printf '\n== %s\n' "$*"; }
fail() { printf 'FAIL: %s\n' "$*" >&2; exit 1; }

PIDS=()
stop_proxies() {
  for p in "${PIDS[@]:-}"; do
    kill "$p" 2>/dev/null || true
    wait "$p" 2>/dev/null || true
  done
  PIDS=()
}
trap 'stop_proxies; rm -f "$ERR"' EXIT

if [[ "${1:-}" == "clean" ]]; then
  ate delete actor "$ACTOR" -a "$ATESPACE" >/dev/null 2>&1 || true
  ate delete actor-template hermes -a "$ATESPACE" >/dev/null 2>&1 || true
  exit 0
fi

HERMES_AGENT_DIR="${HERMES_AGENT_DIR:?name a hermes-agent checkout}"
k -n recall-system get deploy/recall >/dev/null 2>&1 || fail "Recall is not deployed; run deploy/agent-substrate/run-kind.sh in Polign/recall first"

proxy() {
  "$ATE" --context "$CONTEXT" proxy actor "$ACTOR" "$PORT:80" -a "$ATESPACE" >/dev/null 2>&1 &
  PIDS+=("$!")
  for _ in $(seq 1 60); do
    curl -s -o /dev/null -m 2 "localhost:$PORT/readyz" && return 0
    sleep 0.5
  done
  fail "no proxy to $ACTOR"
}

# A failed call prints nothing, so the check after it fails with a message.
call() { curl -s -m 90 -X POST "localhost:$PORT$1" -d "$2" || true; }

new_actor() {
  ate create actor "$ACTOR" -a "$ATESPACE" --template hermes >/dev/null
  ate create egress-policy "$ACTOR" -a "$ATESPACE" -f egress-policy.yaml >/dev/null
  # The gateway caches a missing policy as deny for 10 s, and the plugin stops
  # calling for two minutes after five failures, so let the policy settle.
  sleep 12
}

say "Build the actor image: the plugin on Hermes's provider interface"
ctx="$(mktemp -d)"
mkdir -p "$ctx/hermes/agent" "$ctx/plugin"
cp "$HERMES_AGENT_DIR"/agent/{__init__,jiter_preload,memory_provider,secret_scope}.py "$ctx/hermes/agent/"
cp "$HERMES_AGENT_DIR/hermes_constants.py" "$ctx/hermes/"
cp ../__init__.py "$ctx/plugin/"
cp driver.py Dockerfile "$ctx/"
docker build -q -t "$REGISTRY/recall-hermes-actor:dev" "$ctx" >/dev/null
rm -rf "$ctx"
docker push -q "$REGISTRY/recall-hermes-actor:dev" >/dev/null
ACTOR_IMAGE="$(docker inspect --format '{{index .RepoDigests 0}}' "$REGISTRY/recall-hermes-actor:dev")"
echo "hermes-agent at $(git -C "$HERMES_AGENT_DIR" rev-parse --short HEAD)"

say "Actor template and one actor"
ate delete actor "$ACTOR" -a "$ATESPACE" >/dev/null 2>&1 || true
ate delete actor-template hermes -a "$ATESPACE" >/dev/null 2>&1 || true
sed "s|ACTOR_IMAGE|$ACTOR_IMAGE|" actor-template.yaml | ate create actor-template -f - >/dev/null
for _ in $(seq 1 90); do
  [[ -n "$(ate get actor-template hermes -a "$ATESPACE" -o json | grep -o '"goldenTag"' || true)" ]] && break
  sleep 2
done
new_actor
proxy

say "1. A completed turn is sent to Recall. The actor holds a placeholder, not a key"
out="$(call /turn '{"user":"I moved to Portland last week, and I edit in vim.","assistant":"Noted."}')"
echo "turn: $out"
[[ "$out" == *'"saved": true'* ]] || fail "the turn was not saved"

say "2. The next turn starts with that memory in front of it"
out="$(call /prefetch '{"query":"which editor do I use?"}')"
echo "prefetch: $out"
[[ "$out" == *"vim"* ]] || fail "the memory did not come back at the start of the next turn"

say "3. The agent's tools reach the same memory"
out="$(call /tool '{"name":"polign_recall_search","args":{"question":"where does the user live"}}')"
echo "polign_recall_search: $out"
[[ "$out" == *"Portland"* ]] || fail "polign_recall_search did not find the turn"

say "4. Suspend the actor. After the resume the provider works without reconnecting by hand"
ate suspend actor "$ACTOR" -a "$ATESPACE" --mode=sync >/dev/null
state="$(ate get actor "$ACTOR" -a "$ATESPACE" | awk 'NR==2 {print $4}')"
echo "$ACTOR is $state"
[[ "$state" == "ACTOR_STATE_SUSPENDED" ]] || fail "$ACTOR did not suspend"
out="$(call /turn '{"user":"My standup is at 9:30 on weekdays.","assistant":"Got it."}')"
echo "turn after resume: $out"
[[ "$out" == *'"saved": true'* && "$out" == *'"turns_in_this_process": 2'* ]] || fail "the turn after the resume was not saved, or the process did not come back from its snapshot"
out="$(call /prefetch '{"query":"when is my standup?"}')"
[[ "$out" == *"9:30"* ]] || fail "the turn written after the resume was not recalled: $out"

say "5. Delete the actor and create it again. A new process, the same memory"
stop_proxies
ate delete actor "$ACTOR" -a "$ATESPACE" >/dev/null
new_actor
proxy
out="$(call /prefetch '{"query":"which editor do I use?"}')"
echo "prefetch in the new actor: $out"
[[ "$out" == *"vim"* && "$out" == *'"turns_in_this_process": 0'* ]] || fail "the new actor did not start fresh with the old memory"

say "All checks passed"
