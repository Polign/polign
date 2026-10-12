#!/usr/bin/env bash
# Stop what up.sh started. Memories and the key stay on disk.
set -euo pipefail
STATE="${RECALL_OPENCLAW_DIR:-$HOME/.local/share/polign/openclaw}/state"
for name in recall-serve polign-server; do
  pidfile="$STATE/$name.pid"
  if [ -s "$pidfile" ] && kill -0 "$(cat "$pidfile")" 2>/dev/null; then
    kill "$(cat "$pidfile")" && echo "$name: stopped"
  else
    echo "$name: not running"
  fi
  rm -f "$pidfile"
done
