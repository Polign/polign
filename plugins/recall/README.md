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

Install [Claude Code](https://code.claude.com/docs/en/quickstart), then the
`recall` command. On macOS with Homebrew:

```sh
brew install polign/tap/recall
```

Or with pip, on macOS, Linux or Windows:

```sh
pip install polign-recall
```

Either way you also get `polign-server`, the database Recall keeps memories in.
Then run:

```sh
recall setup
```

Setup starts a private database on your computer, checks that memory reads and
the MCP tools work, and installs this plugin into Claude. Restart Claude Code,
then try the memory prompts above. The database starts again by itself the
next time Claude connects, and your memories stay in
`~/.config/polign/recall/data`.

If you already have Recall running against a database, keep it: see
[connection settings](docs/setup.md#connect-to-an-existing-database).

### Let Recall work out the facts (optional)

By default Claude decides what to remember and fills in each fact itself. With
recall 0.14 or later, you can give Recall a model to do that instead:

```sh
recall setup -extract-model anthropic:claude-haiku-4-5-20251001
```

Claude then gets three plain-text tools, `remember`, `recall` and `forget`, and
answers come back as sentences with what each one replaced. The model is called
on every remember and forget, needs `ANTHROPIC_API_KEY` (or `OPENAI_API_KEY` for
`openai:<model>`) where Claude Code runs, and receives the text you remember.
`ollama:<model>` runs locally; pick a fast model. Run setup with
`-extract-model none` to go back. Your memories are kept either way.

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

The [setup guide](docs/setup.md) covers other databases, occupied ports, and
`recall doctor`, which checks a saved setup end to end.

For the Python client and Go library, visit [Recall](https://github.com/Polign/recall).
