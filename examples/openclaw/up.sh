#!/usr/bin/env bash
# Start a local memory stack for OpenClaw: polign_db, recall serve in text
# mode, and one Recall key. If `openclaw` is on PATH, also install the Recall
# plugin and point OpenClaw at it. Safe to run again: it reuses what is up.
set -euo pipefail

DATA_DIR="${RECALL_OPENCLAW_DIR:-$HOME/.local/share/polign/openclaw}"
DB_ADDR="${POLIGN_HTTP:-127.0.0.1:23000}"
DB_GRPC="${POLIGN_GRPC:-127.0.0.1:23001}"
RECALL_ADDR="${RECALL_LISTEN:-127.0.0.1:8080}"
EXTRACT_MODEL="${RECALL_EXTRACT_MODEL:-ollama:qwen3:8b}"
STATE="$DATA_DIR/state"
mkdir -p "$DATA_DIR/data" "$STATE"

need() { command -v "$1" >/dev/null 2>&1 || { echo "missing: $1 ($2)" >&2; exit 1; }; }
need polign-server "brew install polign/tap/recall, or pip install polign-recall"
need recall "brew install polign/tap/recall, or pip install polign-recall"
need curl "your package manager"

version_ok() { # recall 0.16.0 or later has `recall serve`
  local v; v=$(recall -version 2>/dev/null || echo 0)
  [ "$(printf '%s\n0.16.0\n' "$v" | sort -V | head -1)" = "0.16.0" ]
}
version_ok || { echo "recall $(recall -version 2>/dev/null) is too old; 0.16.0 or later has recall serve" >&2; exit 1; }

up() { curl -sf "http://$1/healthz" >/dev/null 2>&1; }
wait_up() {
  local n=0
  until up "$1"; do
    n=$((n + 1)); [ $n -ge 60 ] && { echo "$2 did not come up; see $3" >&2; exit 1; }
    sleep 1
  done
}

case "$EXTRACT_MODEL" in
  ollama:*)
    curl -sf http://127.0.0.1:11434/api/tags >/dev/null 2>&1 \
      || { echo "Ollama is not answering on 127.0.0.1:11434; start it, or set RECALL_EXTRACT_MODEL" >&2; exit 1; }
    model="${EXTRACT_MODEL#ollama:}"
    curl -sf http://127.0.0.1:11434/api/tags | grep -q "\"$model\"" \
      || echo "warning: Ollama has no $model yet; run: ollama pull $model" >&2 ;;
  anthropic:*) [ -n "${ANTHROPIC_API_KEY:-}" ] || { echo "RECALL_EXTRACT_MODEL=$EXTRACT_MODEL needs ANTHROPIC_API_KEY" >&2; exit 1; } ;;
  openai:*) [ -n "${OPENAI_API_KEY:-}" ] || { echo "RECALL_EXTRACT_MODEL=$EXTRACT_MODEL needs OPENAI_API_KEY" >&2; exit 1; } ;;
esac

if up "$DB_ADDR"; then
  echo "polign_db: already answering on $DB_ADDR"
else
  nohup polign-server -store "fs:$DATA_DIR/data" -http "$DB_ADDR" -grpc "$DB_GRPC" -telemetry=false \
    >"$STATE/polign-server.log" 2>&1 &
  echo $! >"$STATE/polign-server.pid"
  wait_up "$DB_ADDR" polign-server "$STATE/polign-server.log"
  echo "polign_db: started on $DB_ADDR, data in $DATA_DIR/data"
fi

if up "$RECALL_ADDR"; then
  echo "recall serve: already answering on $RECALL_ADDR"
else
  nohup recall -url "http://$DB_ADDR" serve -text -listen "$RECALL_ADDR" -extract-model "$EXTRACT_MODEL" \
    >"$STATE/recall-serve.log" 2>&1 &
  echo $! >"$STATE/recall-serve.pid"
  wait_up "$RECALL_ADDR" "recall serve" "$STATE/recall-serve.log"
  echo "recall serve: started on $RECALL_ADDR, extraction by $EXTRACT_MODEL"
fi

KEY_FILE="$STATE/recall.key"
if [ ! -s "$KEY_FILE" ]; then
  umask 077
  recall -url "http://$DB_ADDR" key create -namespace openclaw -expires 8760h -note "OpenClaw on $(hostname -s)" \
    | awk '/^key:/{print $2}' >"$KEY_FILE"
  [ -s "$KEY_FILE" ] || { echo "minting the Recall key failed" >&2; exit 1; }
  echo "recall key: minted for namespace openclaw, saved in $KEY_FILE"
else
  echo "recall key: reusing $KEY_FILE"
fi

if ! command -v openclaw >/dev/null 2>&1; then
  cat <<MSG

OpenClaw is not on PATH, so it was not configured. Install it, then run this
script again:  npm install -g openclaw
MSG
  exit 0
fi

oc() { openclaw "$@" 2>&1 | grep -v "can't verify where this plugin came from\|recall: memory at http" || true; }
if ! openclaw plugins inspect recall >/dev/null 2>&1; then
  # --force: OpenClaw cancels an unattended install of a package it cannot
  # trace to a signed build. Review the package at
  # https://www.npmjs.com/package/@polign/openclaw-recall first.
  oc plugins install --force --accept-capabilities @polign/openclaw-recall | tail -1
  openclaw plugins inspect recall >/dev/null 2>&1 || { echo "installing @polign/openclaw-recall failed" >&2; exit 1; }
fi
oc config set plugins.slots.memory recall | tail -1
oc config set plugins.entries.recall.enabled true | tail -1
oc config set plugins.entries.recall.hooks.allowConversationAccess true | tail -1
oc config set plugins.entries.recall.config.url "http://$RECALL_ADDR" | tail -1
oc config set plugins.entries.recall.config.apiKey "$(cat "$KEY_FILE")" | tail -1
oc plugins disable memory-core | tail -1

cat <<MSG

Ready. Restart the OpenClaw gateway so it loads the plugin, then check:
  openclaw plugins inspect recall --runtime
  openclaw recall status
Stop the memory stack with ./down.sh. Memories stay in $DATA_DIR/data.
MSG
