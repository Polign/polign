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

Linux / manual (static binaries, no dependencies):

```sh
V=v0.1.0; OS=$(uname -s | tr A-Z a-z); ARCH=$(uname -m | sed 's/x86_64/amd64/;s/aarch64/arm64/')
curl -fsSL "https://github.com/Polign/polign/releases/download/$V/polign_db_${OS}_${ARCH}.tar.gz" | tar -xz
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
