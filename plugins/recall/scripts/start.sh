#!/bin/sh
# Keep user configuration outside Claude's replaceable plugin cache.
set -eu
recall_home=${POLIGN_RECALL_HOME:-"$HOME/.config/polign/recall"}
if [ -x "$recall_home/launch" ]; then
  exec "$recall_home/launch"
fi
# A launcher that is present but not runnable must stop here. Falling through
# would search PATH and quietly connect to a different database, so memories
# saved against the configured one would look as though they had vanished.
if [ -e "$recall_home/launch" ]; then
  echo "Recall found $recall_home/launch but cannot execute it. Fix its permissions, or run recall setup again, then restart Claude Code." >&2
  exit 1
fi

# No saved setup: run the recall binary directly, connecting to POLIGN_URL
# (default http://localhost:23000). Known install locations are searched as
# well as PATH, so an obsolete binary cannot shadow a working one.
for recall_bin in "${RECALL_BIN:-}" "$(command -v recall 2>/dev/null || true)" /opt/homebrew/bin/recall /usr/local/bin/recall "$HOME/.local/bin/recall"; do
  [ -n "$recall_bin" ] && [ -x "$recall_bin" ] || continue
  # Redirect stdin: this probe inherits Claude's JSON-RPC channel, and a
  # binary that reads it rather than answering -help would consume the
  # handshake. grep is absolute so a broken PATH cannot defeat the search.
  if "$recall_bin" mcp -help </dev/null 2>&1 | /usr/bin/grep -q -- '-config-dir'; then
    exec "$recall_bin" mcp -write
  fi
done

# polign_db 0.13 and earlier hosted the same server as `polign mcp
# -memory-only`; keep manually configured installations of those working.
for recall_cli in "${POLIGN_BIN:-}" "$(command -v polign 2>/dev/null || true)" /opt/homebrew/bin/polign /usr/local/bin/polign "$HOME/.local/bin/polign"; do
  [ -n "$recall_cli" ] && [ -x "$recall_cli" ] || continue
  if "$recall_cli" mcp -help </dev/null 2>&1 | /usr/bin/grep -q -- '-memory-only'; then
    exec "$recall_cli" mcp -memory-only -write
  fi
done
echo 'Recall could not find the recall binary. Install it (brew install polign/tap/recall, or pip install polign-recall), run recall setup, and restart Claude Code.' >&2
exit 1
