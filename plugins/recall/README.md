# Recall for Claude Code

This plugin connects Claude Code to [Recall](https://github.com/Polign/recall),
typed memory that agents can share across applications and sessions. Recall also
works through Go, Python, and other MCP hosts.

Ask Claude to remember your preferences and project details, then pick up where
you left off in a new conversation. When something changes, update the memory.
You can always ask what was saved before.

```text
/recall:recall-memory Remember that I prefer concise answers.
```

In a new session:

```text
/recall:recall-memory What answer style do I prefer?
```

Recall stores your memories in [Polign](https://github.com/Polign/polign). The
setup below keeps them on your computer. It uses Claude's existing model, so
there's no extra model to install or embedding API key to obtain.

## Get started

Install [Claude Code](https://code.claude.com/docs/en/quickstart) and
[Polign v0.6.4 or later](https://github.com/Polign/polign#install) first.
If you already have Recall running, keep your existing database and see
[connection settings](docs/setup.md#connect-to-an-existing-database).

**Start the database in your terminal:**

```sh
polign-server -store "fs:$HOME/.local/share/recall/data"
```

Leave that terminal open. Open another terminal and start Claude:

```sh
claude
```

**Run these commands one at a time inside Claude:**

```text
/plugin marketplace add Polign/polign
```

```text
/plugin install recall@polign
```

Restart Claude Code, then try the memory prompts above. Memories stay in
`~/.local/share/recall/data` after you stop the database; start it again with the
same command when you need it.

## A few things to try

After `/recall:recall-memory`, you can ask:

| What you want to do | What to say |
| --- | --- |
| Save a preference | Remember that I use neovim. |
| Save a project detail | Remember that project "recall-demo" uses pytest. |
| Look something up | Which test framework does recall-demo use? |
| Change a preference | Remember that I now prefer detailed answers. |
| Look back | Show the history of my answer-style preference. |
| Forget a preference | Forget my preferred editor. |

Forgetting removes a fact from current answers and keeps it in the history.
It does not permanently delete the record.

## Need help connecting?

Run `/mcp` inside Claude to see the Recall connection's status. If it failed,
check that your database is still running, then restart Claude to retry.

The [setup guide](docs/setup.md) covers occupied ports, multiple Polign
installations, other databases, and the upcoming `polign recall setup` command.
That command is still in development and is not part of Polign v0.6.5.

For the Python client and Go library, visit [Recall](https://github.com/Polign/recall).
