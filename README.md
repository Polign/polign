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

## Kubernetes

Deploy Polign with the [Helm chart](charts/polign). The chart runs one server
against your bucket, with API-key authentication and recovery across pod
replacement. Set up the bucket identity as described in the chart README, then:

```sh
helm install polign oci://ghcr.io/polign/charts/polign \
  --version 0.1.0 --set store.uri=s3://your-bucket/polign
```

The [server container](deploy/container) supports Linux AMD64 and ARM64.
Chart 0.1.0 uses Polign 0.6.5. Upgrades briefly interrupt service.

## Recall agent memory

The [Recall plugin](plugins/recall) gives Claude Code memory across sessions:
remember preferences and project facts, correct them, and inspect their history.
With Polign v0.6.4+, `polign mcp -memory-only -write` needs no embedding service
or custom schema. The host agent extracts facts; Recall validates and resolves them.

```text
/plugin marketplace add Polign/polign
/plugin install recall@polign
```

The [Recall Python client](https://github.com/Polign/recall/tree/main/python)
uses the same memory service. [Recall library and documentation](https://github.com/Polign/recall).

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

### LangChain

`langchain-polign` provides `PolignVectorStore`, a LangChain vector store
over one collection, with metadata filters, relevance scores, MMR, and
lexical or hybrid search on servers with a segment store.

```sh
pip install langchain-polign
```

```python
from langchain_polign import PolignVectorStore

store = PolignVectorStore(embedding=embeddings, collection="docs", url="http://localhost:23000")
store.add_texts(["cats purr", "dogs bark"], metadatas=[{"lang": "en"}] * 2)
store.similarity_search("purring", k=1, filter={"lang": "en"})
```

See [python/langchain-polign/](python/langchain-polign/) for how documents
are stored and the full API.

### LlamaIndex

`llama-index-vector-stores-polign` provides `PolignVectorStore` for
LlamaIndex, with metadata filters, `delete_ref_doc`, MMR, and text or hybrid
query modes on servers with a segment store.

```sh
pip install llama-index-vector-stores-polign
```

```python
from llama_index.core import StorageContext, VectorStoreIndex
from llama_index.vector_stores.polign import PolignVectorStore

store = PolignVectorStore(collection_name="docs", url="http://localhost:23000")
index = VectorStoreIndex.from_documents(
    documents, storage_context=StorageContext.from_defaults(vector_store=store)
)
index.as_retriever(similarity_top_k=5).retrieve("what purrs?")
```

See [python/llama-index-vector-stores-polign/](python/llama-index-vector-stores-polign/).

## License

The client libraries, the proto file, and everything else in this repository
are licensed under the [Apache License 2.0](LICENSE). The server binaries
attached to releases ship with their own license inside each archive.

## Issues

Bug reports and questions are welcome in this repository's
[issue tracker](https://github.com/Polign/polign/issues).
