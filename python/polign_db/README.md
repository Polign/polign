# polign_db

The [polign_db](https://polign.com/polign-db.html) server and command-line
client, packaged so that pip can install them.

```bash
pip install polign_db
```

This puts two programs into your environment, next to `python`:

| Program | What it is |
|---|---|
| `polign-server` | The database server |
| `polign` | The CLI: put, get, search, and the `polign mcp` server that Recall uses |

They are the same binaries as the
[GitHub release](https://github.com/Polign/polign/releases) with the same
version number. Wheels exist for Linux (x86_64 and arm64, glibc and musl),
macOS 12 or newer (Apple silicon and Intel), and Windows (x86_64 and arm64).
Nothing is compiled and nothing is downloaded at install time or at run time.

## Start a server

```bash
polign-server -store fs:./polign-data
```

That serves HTTP on `http://localhost:23000` and keeps its data in
`./polign-data`. Use `s3://bucket/prefix`, `gcs://` or `az://` instead of
`fs:` to store into a bucket. [Get started](https://polign.com/developers.html)
covers the rest.

## Find the binaries from Python

An activated environment has both programs on `PATH`. Code that runs without
activation, such as `/srv/app/.venv/bin/python worker.py`, can ask for the path:

```python
import polign_db

polign_db.find_bin("polign")          # /srv/app/.venv/bin/polign
polign_db.find_bin("polign-server")
```

## Which package do I want?

| Package | What it installs |
|---|---|
| `polign_db` | The server and CLI binaries (this package) |
| [`polign`](https://pypi.org/project/polign/) | The Python client for a running server |
| [`polign-recall`](https://pypi.org/project/polign-recall/) | Typed agent memory; depends on this package |
| [`recall-livekit`](https://pypi.org/project/recall-livekit/) | Recall memory for LiveKit voice agents |

## License

The binaries are free to use under the polign_db Software License, which is
included in the wheel as `LICENSE`. The packaging script in this directory is
Apache-2.0 like the rest of the repository.
