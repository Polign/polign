# Polign

Object-store-native vector database: one static binary, durability from your
own bucket. This repository is the home for the **official binary releases**
and the **open source client libraries**. The server's source is not public.

Docs, architecture, and guides: **[polign.com](https://polign.com)**

## Install

macOS (Homebrew):

```sh
brew install polign/tap/polign
```

Linux and macOS (static binaries, no dependencies):

```sh
curl -fsSL https://get.polign.com | sh
```

The script picks the build for your OS and architecture, checks the archive
against the release `checksums.txt`, and installs to `/usr/local/bin` when that
is writable and `~/.local/bin` otherwise. With no version set it installs the
latest stable release. Set `POLIGN_VERSION` to pin one, and `POLIGN_BIN_DIR` to
choose where the binaries land:

```sh
# replace vX.Y.Z with a tag from the releases page
curl -fsSL https://get.polign.com | POLIGN_VERSION=vX.Y.Z sh
```

Release tags are listed at
[github.com/Polign/polign/releases](https://github.com/Polign/polign/releases),
and `https://dl.polign.com/latest/version` prints the latest stable tag if you
need to resolve it in a script.

To download an archive yourself instead of running the script, fetch it from
`dl.polign.com`, which always redirects to the latest stable release:

```sh
OS=$(uname -s | tr '[:upper:]' '[:lower:]')
ARCH=$(uname -m | sed 's/x86_64/amd64/;s/aarch64/arm64/')
curl -fsSL "https://dl.polign.com/latest/polign_db_${OS}_${ARCH}.tar.gz" | tar -xz
```

Swap `latest` for a tag to pin. Or grab the archive by hand from the
[latest release page](https://github.com/Polign/polign/releases/latest).

Each archive ships six binaries: `polign` (CLI), `polign-server`,
`polign-persistor`, `polign-apikey`, `polign-maintain`, and `polign-import`.

## Quick start

```sh
# a durable, tiered deployment from one flag: write log, cold-first serving,
# hot tier, disk cache, and index maintenance against your bucket
polign-server -store s3://your-bucket/prefix

# or try it locally
polign-server -store fs:/var/lib/polign
```

`sha256` checksums for every archive are attached to each release as
`checksums.txt`.

## Python client

The `polign` package is a thin client with two interchangeable transports:
HTTP with no dependencies, and gRPC through the `[grpc]` extra.

```sh
pip install polign
pip install "polign[grpc]"
```

```python
from polign import Client

client = Client("http://localhost:23000")
client.put("docs", "doc-1", embedding, metadata={"title": "Cats"})
for hit in client.search("docs", values=query_embedding, k=10):
    print(hit.id, hit.distance, hit.metadata)
```

Source, tests, and the full operations reference live in [python/](python/).
The gRPC wire contract is [proto/vectordb.proto](proto/vectordb.proto).
Framework integrations (LangChain, LlamaIndex) will be built in this
repository on top of the client.

## License

The client libraries, the proto file, and everything else in this repository
are licensed under the [Apache License 2.0](LICENSE). The server binaries
attached to releases ship with their own license inside each archive.

## Issues

Bug reports and questions are welcome in this repository's
[issue tracker](https://github.com/Polign/polign/issues).
