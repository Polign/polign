# Recall for Claude Code

Memory that survives sessions, accepts corrections, and keeps its history.
Recall is v0.3.0. The Claude plugin and Polign binaries have separate versions.

## Simplified setup (in development)

This flow requires a Polign build containing `recall setup`; it is not in the
published v0.6.5 binaries yet. Install Claude Code first, then run:

```sh
polign recall setup
```

Setup finds Claude, checks its version, starts a private local database on free
ports, verifies a memory read and the MCP tools, and installs the Recall plugin.
Restart Claude Code when setup finishes. No second terminal, port selection,
embedding service, model download, or schema file is needed.

Setup uses the CLI you invoke and the server next to it. If an older installation
shadows Homebrew, invoke the Homebrew binary directly:

```sh
"$(brew --prefix)/bin/polign" recall setup
```

Ask Claude:

```text
/recall:recall-memory Remember that I prefer concise answers.
```

Start a new session and ask:

```text
/recall:recall-memory What answer style do I prefer?
```

Then ask it to remember that you now prefer detailed answers, and ask for the
history of your answer-style preference. The current answer should change and
the previous statement should remain in the history.

## Keep an existing database

Point setup at the server and collection that already contain your memories:

```sh
polign recall setup -url http://127.0.0.1:23100 -collection recall_dogfood
```

For an authenticated server, set `POLIGN_API_KEY` before setup. On the first run,
setup also accepts `POLIGN_URL`, `POLIGN_COLLECTION`, and `POLIGN_PREDICATES`.
Subsequent runs preserve the saved connection; use explicit `-url` and
`-collection` options to change it, or `-local` for the managed local database.
Changing connections does not copy memories between databases.

Configuration, the launcher, and local data live in `~/.config/polign/recall/`,
outside Claude's replaceable plugin cache. Configuration and the local API key
are owner-readable only. The launcher saves the CLI's absolute path so later
PATH changes do not select another installation.

The local server runs in the background, binds only to loopback, and requires a
generated API key. After a reboot or server exit, the next Recall connection
starts it against the same data directory. It does not start at login. Logs are
in `~/.config/polign/recall/server.log`. Setup checks connectivity without writing
sample facts. Saved settings take precedence over environment variables inherited
by Claude. For another configuration directory, set `POLIGN_RECALL_HOME` before
both setup and Claude.

If something fails:

```sh
polign recall doctor
```

Doctor checks the saved executable, database readiness, authenticated memory
read, and MCP handshake/tools. It does not start a stopped server or write
memories. Restart Claude after fixing a connection that Claude has cached as
failed, then inspect `/mcp`. Run `claude update` if Claude reports that its model
requires a newer client; setup cannot predict future model requirements.

## Published versions and manual setup

The published plugin works with Polign v0.6.4+ and Recall v0.3.0. Until simplified
setup is released, start a database yourself:

```sh
polign-server -store fs:./recall-data
```

Then run these **separate commands inside Claude Code** and restart Claude:

```text
/plugin marketplace add Polign/polign
/plugin install recall@polign
```

Existing manual connections still use `POLIGN_URL`, `POLIGN_API_KEY`, and
`POLIGN_COLLECTION` when no saved setup exists. The new launcher searches PATH
and common installation locations for a compatible CLI. From this checkout, use
`claude --plugin-dir ./plugins/recall` to test it.

The default collection is `recall_lexical_v1`, with 15 starter predicates and
built-in lexical retrieval. Claude proposes facts; Recall validates them and
resolves corrections. Never mix lexical vectors and another model's embeddings
in one collection.

This plugin enables writes. For another MCP host, run setup with `-no-plugin`
and configure `polign recall mcp`. For a read-only connection, configure
`polign mcp -memory-only` against your server without `-write`.
