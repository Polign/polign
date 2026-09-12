---
name: polign-client
description: >
  Write application code against a running polign_db server.
  Use when the user is storing or searching vectors with Polign from code:
  the `polign` Python package (Client, GrpcClient, put, put_many, search,
  filters, hybrid search, Fusion, typed metadata, errors), the
  `langchain-polign` and `llama-index-vector-stores-polign` integrations, the
  HTTP/JSON API under /v1/collections, the gRPC contract in vectordb.proto,
  or the `polign` command line client.
  Also use for "how do I query Polign", "Polign metadata filter", "Polign
  hybrid search", or building RAG and agent memory on Polign.
  Do not use for starting, configuring, or operating the server itself; that
  is the polign-server skill.
---

# Writing code against polign_db

Polign stores a vector, an id, and a metadata map per record, and searches by
vector similarity, by keyword, or by both at once. The client libraries are
Apache 2.0 and live at https://github.com/Polign/polign.

Two rules worth following before writing any code:

1. **Ask the server for the collection's shape.** Vector dimension, distance
   metric, index type, and the metadata field the keyword index reads are all
   properties of the collection. Getting the dimension wrong is the single
   most common error, and it is avoidable.
2. **The embedding model is the caller's job.** Polign does not embed
   anything. Whatever produced the stored vectors must produce the query
   vector too, with the same model and the same normalisation.

## Python

```sh
pip install polign            # HTTP transport, no dependencies
pip install "polign[grpc]"    # adds the gRPC transport
```

```python
from polign import Client

with Client("http://localhost:23000") as client:
    print(client.describe_collection("docs"))   # dimension, metric, index_type,
                                                # segment_backed, text_field
    client.put("docs", "doc-1", embedding, metadata={"title": "Cats", "year": 2026})
    for hit in client.search("docs", values=query_embedding, k=10):
        print(hit.id, hit.distance, hit.score, hit.metadata)
```

`Client` takes `api_key=` for a server started with `-require-data-key`,
plus `timeout=` and `ssl_context=`. `GrpcClient` has the same operations over
`localhost:23001` and takes `credentials=` and `channel_options=`. The two are
interchangeable, so write against whichever fits the deployment and do not mix
them in one code path for no reason.

Operations: `put`, `put_many`, `get`, `get_many`, `list`, `delete`,
`delete_many`, `update_metadata`, `search`, `describe_collection`, `health`.
On a server with `-byo-store`, also `create_collection`, `get_collection`,
`list_collections`, `delete_collection`, `verify_collection`, `backend_setup`.

### Writing

Batch. A single `put` per record is the slow path.

```python
from polign import Vector

client.put_many("docs", [
    Vector(id="doc-1", values=v1, metadata={"lang": "en", "year": 2026}),
    Vector(id="doc-2", values=v2, metadata={"lang": "fr", "year": 2025}),
])
```

A batch holds at most 5000 vectors, so chunk anything larger. The server
validates the whole batch before applying any of it: every vector needs an
id, non-empty values, and the same dimension, or the call fails having written
nothing. The client does not retry an ambiguous failure on its own, because a
rarer mid-batch server failure can leave earlier vectors applied. Reconcile
state before deciding to retry.

Writes are upserts. Putting an id that already exists replaces that record,
which makes ids the deduplication key: derive them from something stable in
the source rather than generating a fresh uuid per run.

To change metadata without re-embedding anything, patch it:

```python
client.update_metadata("docs", ["doc-1", "doc-2"],
                       set={"reviewed": True}, unset=["draft"])
```

Keys in `set` are written, keys in `unset` are removed, and every other key
keeps its value. The stored vectors are untouched.

Metadata values are strings, numbers, booleans, and flat lists of those.
Numbers and booleans are stored typed, so a range filter on a number compares
numerically rather than as text. Pass `typed_metadata=True` on reads to get
them back with their types instead of as strings.

### Filters

The filter language is the same dict on `search` and on `list`. A plain
mapping is equality, ANDed across keys:

```python
client.search("docs", values=q, k=10, filter={"lang": "en"})
```

Per-key operators are `$eq`, `$ne`, `$in`, `$gt`, `$gte`, `$lt`, `$lte`, and
`$exists`, and they compose with `$and`, `$or`, and `$not`:

```python
client.search("docs", values=q, k=10, filter={
    "ts": {"$gte": "2026-01-01"},
    "lang": {"$in": ["en", "fr"]},
})
```

`list` takes the same filters. With one, `offset` and the page's `total` count
matching vectors only.

### Keyword and hybrid search

```python
client.search("docs", text="purring cats", k=10)                 # BM25 keyword
client.search("docs", values=q, text="purring cats", k=10)       # hybrid
```

Keyword search reads one metadata field, the one `describe_collection` reports
as `text_field`, so text meant to be findable by keyword goes in that field at
write time. It needs a segment-backed collection; check `segment_backed`
before promising a user hybrid search will work.

Hybrid results are fused server-side. Default fusion is reciprocal rank
fusion, and `Fusion` tunes it:

```python
from polign import Fusion

client.search("docs", values=q, text="cats", k=10,
              fusion=Fusion(method="linear", alpha=0.7))   # 0.7 weight on the vector leg
```

`method` is `"rrf"` (with `rrf_k`, default 60) or `"linear"` (with `alpha` in
[0, 1], default 0.5, the weight of the vector leg).

Hybrid is not automatically better than semantic search. On a large corpus it
can lose to plain vector search, so measure both against real queries before
shipping it as the default.

### Search knobs

Defaults are fine until a measurement says otherwise. In rough order of how
often they earn their keep:

- `nprobe`: on a cold read, how many IVF cells to probe. This is the main
  recall lever. Raise it when recall is short, at the cost of latency.
- `k`: ask for what will be used. Over-fetching costs on every query.
- `rescore`: on a compressed IVF-PQ collection, the size of the exact rescore
  pool. 0 is the server default, a negative value ranks by the approximate
  fast tier alone. Other collection types rank exactly and ignore it.
- `ef`: HNSW beam width, 0 for the server default.
- `cold=True`: force the object-store path for a query.

`Hit` carries `id`, `distance`, `score`, and `metadata`. `distance` is in the
collection's metric, where lower is nearer; `score` is the relevance score and
is what hybrid fusion ranks by. Stored vectors are never returned by a search.

### Errors

Everything derives from `PolignError`, so catch that at a boundary and the
specific ones where the response differs:

| Error | Meaning |
| --- | --- |
| `InvalidArgumentError` | dimension mismatch, bad id, k <= 0 |
| `NotFoundError` | no such collection or vector |
| `AuthenticationError` | missing or invalid API key |
| `PermissionDeniedError` | key exists, not allowed for this operation |
| `RateLimitError` | admission capacity exhausted, retry with backoff |
| `ConflictError` | concurrent write conflict |
| `NotEnabledError` | the server was not started with the flag this needs |
| `NotOwnerError` | fleet mode: another node owns this collection |
| `UnavailableError`, `ConnectionError`, `ServerError` | transport or server side |

`RateLimitError` is the one to handle deliberately. It is retryable, and it
means the server refused the write before logging it, so a retry after backoff
is safe.

A `NotEnabledError` from collection calls means the server is running without
`-byo-store`. Collections are implicit there: writing to a name creates it,
and there is no registry to list.

## LangChain

```sh
pip install langchain-polign
```

```python
from langchain_polign import PolignVectorStore

store = PolignVectorStore(embedding=embeddings, collection="docs",
                          url="http://localhost:23000")
store.add_texts(["cats purr", "dogs bark"], metadatas=[{"lang": "en"}] * 2)
store.similarity_search("purring", k=1, filter={"lang": "en"})
```

Supports metadata filters, relevance scores, MMR, and keyword or hybrid search
on a segment-backed server. Full API in `python/langchain-polign/`.

## LlamaIndex

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

Supports metadata filters, `delete_ref_doc`, MMR, and text or hybrid query
modes. Full API in `python/llama-index-vector-stores-polign/`.

## Other transports

**HTTP/JSON** needs no client library. The data plane is under
`/v1/collections/{collection}`: `PUT|GET|DELETE .../vectors/{id}`,
`POST .../vectors:batch`, `POST .../vectors:get`, `POST .../vectors:delete`,
`POST .../vectors:update`, `GET .../vectors` to list, `GET .../describe`, and
`POST .../query` to search. An API key rides as `Authorization: Bearer`.

```sh
curl -fsS localhost:23000/v1/collections/docs/query \
  -H 'Content-Type: application/json' \
  -d '{"text": "purring cats", "k": 5}'
```

**gRPC** is on port 23001 by default. The contract is
[proto/vectordb.proto](https://github.com/Polign/polign/blob/main/proto/vectordb.proto),
and clients for other languages generate from it.

**Command line**, useful for checking an assumption before writing code
against it:

```sh
polign search docs -text "purring cats" -k 5
polign get docs doc-1
polign list docs
```

`polign` reads `POLIGN_URL` and `POLIGN_API_KEY`, and the global `-url` and
`-key` flags go before the subcommand.
