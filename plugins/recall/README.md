# Recall for Claude Code

Memory that survives sessions, accepts corrections, and keeps its history.
Requires the Polign CLI/server release with Recall v0.3.0 support (v0.6.4).

Start a durable local database:

```sh
polign-server -store fs:./recall-data
```

In Claude Code:

```text
/plugin marketplace add Polign/polign
/plugin install recall@polign
```

Restart Claude Code after installation. From a checkout, use
`claude --plugin-dir ./plugins/recall` to load this plugin directly.

Ask it to remember your preferred editor, start another session, and ask which
editor you use. Correct the preference and ask to see its history.

The plugin starts `polign mcp -memory-only -write`. The default collection is
`recall_lexical_v1`, with 15 starter predicates and built-in lexical retrieval.
There is no embedding API, second model runtime, or model download. Claude
proposes facts from text; Recall validates them and resolves corrections.

Set `POLIGN_URL`, `POLIGN_API_KEY`, or `POLIGN_COLLECTION` in the environment
before starting Claude Code to use a different server, namespaced credential,
or dedicated collection. `POLIGN_PREDICATES` selects a custom registry JSON file.
Never mix lexical vectors with another model's embeddings in one collection.

This plugin enables memory writes. For a read-only connection, configure
`polign mcp -memory-only` without `-write` as an MCP server instead.
