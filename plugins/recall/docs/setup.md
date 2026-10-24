# Recall connection settings

For a first local installation, follow the [plugin README](../README.md).
This page covers other connections and help with setup problems.

## Connect to an existing database

Keep using the server and collection that contain your memories. To connect a
manual plugin installation to a server on port 23100, set its address before
starting Claude:

```sh
export RECALL_BACKEND=polign
export RECALL_BACKEND_URL=http://127.0.0.1:23100
export RECALL_COLLECTION=recall_dogfood
claude
```

The same three variables name any backend: `RECALL_BACKEND` is `polign`,
`pgvector`, `mongodb` or `qdrant`, `RECALL_BACKEND_URL` is the database in
that backend's own form, and `RECALL_BACKEND_KEY` is the credential when the
URL does not carry one. A collection is a named group of memories in the
database. The default is `recall_lexical_v1`. Changing the connection or
collection does not copy your existing memories into it. `RECALL_PREDICATES`
selects a custom memory registry JSON file. Keep these environment settings in
the terminal that starts Claude. The names used before recall 0.23
(`POLIGN_URL`, `POLIGN_API_KEY`, `POLIGN_COLLECTION`) still work.

If you have run `recall setup`, its saved configuration takes precedence over
these environment variables. Change it with the command below.
A custom `env` entry in an installed plugin's `.mcp.json` also overrides the
terminal environment; update that entry if you previously configured one.

## Fix a connection problem

| What you see | What to do |
| --- | --- |
| `claude: command not found` | Follow the [Claude Code installation guide](https://code.claude.com/docs/en/quickstart), then open a new terminal. |
| Claude says its version is too old for the model | Update Claude using the method you installed it with. For the native installer, run `claude update`; for Homebrew, run `brew upgrade claude-code`. |
| `bind: address already in use` | Another process is using the port. If it is your existing Polign database, connect to it. To start a separate database, use the example below. |
| Recall could not find the recall binary | Install it with `brew install polign/tap/recall` or `pip install polign-recall`, then check `which -a recall`. |
| Recall failed to connect or is waiting to retry | Confirm the database is running and its address is correct. Restart Claude, then check `/mcp`. |
| The marketplace address is rejected | Run `/plugin marketplace add Polign/polign` and `/plugin install recall@polign` as two separate commands inside Claude. |

To start a separate local database on different ports:

```sh
polign-server -store "fs:$HOME/.local/share/recall/another-database" \
  -http 127.0.0.1:23100 -grpc 127.0.0.1:23101
```

Then set `RECALL_BACKEND=polign` and `RECALL_BACKEND_URL=http://127.0.0.1:23100`
in the terminal that starts Claude.
Both ports must be free. This example creates a separate database; it does not
move memories from your original one.

Without a saved setup, the plugin looks for `recall` on PATH and in common
installation locations and runs `recall mcp -write`, which with nothing else
set keeps memory in a local database in `~/.local/share/recall`; it falls back
to the `polign mcp` server of polign_db 0.13 and earlier. A saved setup pins the executable path. If the saved launcher exists but cannot run, fix its permissions or rerun
setup; the plugin reports the problem instead of selecting another database.

## Setup

`recall setup` checks Claude's version, starts a local database on available
ports, verifies a memory read and the MCP tools, and installs the plugin.
Restart Claude when it finishes. It checks connectivity without writing sample
memories.

For an existing database:

```sh
recall setup -backend polign -url http://127.0.0.1:23100 -collection recall_dogfood
recall setup -backend qdrant -url http://localhost:6333
```

Pass `-key` or set `RECALL_BACKEND_KEY` before setup if that database requires
a credential the URL does not carry. On the first run, setup also reads
`RECALL_BACKEND`, `RECALL_BACKEND_URL`, `RECALL_COLLECTION` and
`RECALL_PREDICATES`. Later runs preserve the saved connection. Use `-backend`,
`-url` and `-collection` to change it, or `-local` to use the managed local
database.

The local database runs `polign-server`. Setup uses the one installed beside
`recall`, then the first on PATH; pass `-server` to choose another.

### Where setup saves things

| Item | Location |
| --- | --- |
| Connection settings and launcher | `~/.local/share/recall/` |
| Managed local database | `~/.local/share/recall/data/` |
| Local server logs | `~/.local/share/recall/server.log` |

A setup made before recall 0.23 lives in `~/.config/polign/recall/` and stays
there. These files stay outside Claude's plugin cache, so updating the plugin
does not remove them. Settings and the local API key are readable only by your
user. The launcher saves the `recall` binary's absolute path to avoid selecting
another version when PATH changes. For another directory, set `RECALL_HOME`
before both setup and Claude.

The managed server runs in the background, listens only on your computer, and
requires its generated API key. After a reboot or server exit, the next Recall
connection starts it against the same data directory. It does not start at login.

To check a saved setup:

```sh
recall doctor
```

Doctor says which database the flags, the environment and the saved setup
resolve to and where that came from, then checks the connection, a memory
read, and the MCP handshake and tools. It starts a stopped local database
and writes no memories. After fixing a cached
connection failure, restart Claude and check `/mcp`.

## Other MCP hosts and custom memory types

For another MCP host, use `recall mcp -write` with the connection environment
variables above, or run `recall setup -no-plugin` and use `recall mcp`, which
then serves the saved connection.

The plugin allows memory writes. For a read-only connection, configure
`recall mcp` without `-write`.

Recall starts with 15 memory types and built-in word-overlap search. Claude
proposes facts; Recall checks their types and applies the correction rules.
Use a separate collection if you switch to a model-based embedding method.
See the [Recall documentation](https://github.com/Polign/recall) for details.

To test the plugin directly from this repository:

```sh
claude --plugin-dir ./plugins/recall
```
