# Polign

Object-store-native vector database: one static binary, durability from your
own bucket. This repository is the home for **official binary releases** —
Polign's source is not public.

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
is writable and `~/.local/bin` otherwise. Set `POLIGN_VERSION` to pin a release
and `POLIGN_BIN_DIR` to choose where the binaries land:

```sh
curl -fsSL https://get.polign.com | POLIGN_VERSION=v0.2.2 sh
```

Each archive ships five binaries: `polign` (CLI), `polign-server`,
`polign-persistor`, `polign-apikey`, and `polign-maintain`.

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

## Issues

Bug reports and questions are welcome in this repository's
[issue tracker](https://github.com/Polign/polign/issues).
