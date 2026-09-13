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
  echo "Recall found $recall_home/launch but cannot execute it. Fix its permissions, or run polign recall setup again, then restart Claude Code." >&2
  exit 1
fi

# Preserve manually configured v0.1.0 installations. Search known install
# locations as well as PATH so an obsolete CLI cannot shadow a working one.
for recall_cli in "${POLIGN_BIN:-}" "$(command -v polign 2>/dev/null || true)" /opt/homebrew/bin/polign /usr/local/bin/polign "$HOME/.local/bin/polign"; do
  [ -n "$recall_cli" ] && [ -x "$recall_cli" ] || continue
  # Redirect stdin: this probe inherits Claude's JSON-RPC channel, and a
  # binary that reads it rather than answering -help would consume the
  # handshake. grep is absolute so a broken PATH cannot defeat the search.
  if "$recall_cli" mcp -help </dev/null 2>&1 | /usr/bin/grep -q -- '-memory-only'; then
    exec "$recall_cli" mcp -memory-only -write
  fi
done
echo 'Recall could not find a compatible Polign CLI. Install or upgrade Polign, then run polign recall setup and restart Claude Code.' >&2
exit 1
