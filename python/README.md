# polign — Python client for polign_db

A thin Python client for [polign](https://polign.com) with
two interchangeable transports:

- **HTTP** (`polign.Client`) — zero dependencies, pure stdlib. Talks JSON to
  the server's HTTP listener (default `:23000`).
- **gRPC** (`polign.GrpcClient`) — install with the `[grpc]` extra.
  Talks to the gRPC listener (default `:23001`).

Both expose the same operations with identical semantics: the data plane
(`put`, `put_many`, `get`, `get_many`, `list`, `delete`, `delete_many`,
`search`, `describe_collection`) and
collection management (`create_collection`, `get_collection`,
`list_collections`, `delete_collection`, `verify_collection`).

## Install

```bash
pip install polign             # HTTP client, no dependencies
pip install 'polign[grpc]'     # + gRPC transport (grpcio, protobuf)
```

The package has its own version line and does not track the server version.
Features that need a newer server say so where they are described (for
example typed metadata needs v0.3.0+); everything else works against any
0.x server.

## Quick start

```python
from polign import Client

client = Client("http://localhost:23000")

# Upsert. Collections are auto-created on first put, inferring their
# dimension from the vector. Values accept lists or numpy arrays.
client.put("docs", "doc-1", embedding, metadata={"title": "Cats", "url": "/cats"})

# Nearest-neighbour search (distance: smaller = closer)
for hit in client.search("docs", values=query_embedding, k=10):
    print(hit.id, hit.distance, hit.metadata)
```

Swap in gRPC by changing two lines — the rest of the code is identical:

```python
from polign import GrpcClient

client = GrpcClient("localhost:23001")
```

## Operations

```python
client.put("docs", "doc-1", values, metadata={"k": "v"})  # upsert, returns id
client.put_many("docs", [Vector(id="a", values=va), Vector(id="b", values=vb)])
                                               # batch upsert, one request
v = client.get("docs", "doc-1")                # Vector(id, values, metadata)
vs = client.get_many("docs", ["a", "b"])       # batch read, byte-exact values:
                                               # never a compressed reconstruction
                                               # (get may return one on a cold-
                                               # flushed collection); unknown ids
                                               # omitted, request order kept
page = client.list("docs", limit=100, offset=0)  # page.vectors, page.total
page = client.list("docs", filter={"user_id": "u1"})
                                               # filtered listing: same dict
                                               # language as search; offset and
                                               # page.total count matches only,
                                               # so pagination works unchanged
client.delete("docs", "doc-1")                 # True; False if known absent
client.delete_many("docs", ids=["a", "b"]).ids  # deletes durably recorded
result = client.delete_many("docs", filter={"ref_doc_id": "source-42"})
                                               # hot + cold metadata delete;
                                               # result.ids, and result.truncated
                                               # when >5000 matched: repeat the
                                               # call until it is False
desc = client.describe_collection("docs")      # dimension, metric, index type,
                                               # segment/text capabilities
hits = client.search("docs", values=q, k=10)   # [Hit(id, distance, score, metadata)]
```

Delete responses omit ids known to be absent. On a cold-only collection with
no in-memory id index, point existence is not cheaply knowable, so an explicit
delete is durably recorded and reported optimistically; replaying it for an
actually absent id is harmless.

### Typed metadata

Metadata values may be strings, numbers, or booleans; numbers and booleans
are stored typed, and filters compare them by type (`{"score": {"$gt": 0.5}}`
matches numerically). By default reads return every value as a string, so
existing code keeps working; pass `typed_metadata=True` to `get`, `get_many`,
`list`, or `search` to get stored types back:

```python
client.put("docs", "m1", values, metadata={"topic": "SIP", "score": 0.85, "published": True})
v = client.get("docs", "m1")                        # {"score": "0.85", ...}  (strings)
v = client.get("docs", "m1", typed_metadata=True)   # {"score": 0.85, "published": True, ...}
hits = client.search("docs", values=q, k=10, filter={"score": {"$gt": 0.5}})
```

Typed values need a server with typed-metadata support (v0.3.0+); all-string
metadata works against any server version.

### Search options

```python
from polign import Fusion

client.search(
    "docs",
    values=q,                      # vector leg (either values or text required)
    k=10,
    ef=64,                         # HNSW beam width override (0 = server default)
    filter={"lang": "en"},         # metadata predicate (see below)
    text="quick brown fox",        # BM25 leg (needs a segment index server-side)
    fusion=Fusion(method="linear", alpha=0.6),  # hybrid fusion; default RRF
    cold=True, nprobe=8,           # serve from object-store segments
)
```

`text` alone runs a pure BM25 search; `values` + `text` runs hybrid search
fused server-side. `hit.score` is the BM25/fused relevance (larger = better)
and is `0.0` on a pure vector search.

`filter` takes the same dict language on both transports: bare values are equality
(ANDed across keys); per-key operator objects (`$eq`, `$ne`, `$in`, `$gt`,
`$gte`, `$lt`, `$lte`, `$exists`) and the composers `$and`/`$or`/`$not`
express richer predicates:

```python
filter={
    "tenant": "acme",
    "score": {"$gte": 0.5},
    "$or": [{"lang": "en"}, {"lang": {"$exists": False}}],
}
```

## Collection management

Collections are auto-created on first put, so most applications never touch
these. On a server started with `-byo-store`, the collection API additionally
binds collections to customer-owned buckets — and it takes an API key:

```python
from polign import CollectionBackend

client = Client("https://db.example.com:23000", api_key="plgn_<key_id>_<secret>")

info = client.create_collection(
    "docs", CollectionBackend(uri="s3://my-bucket/docs", role_arn="arn:aws:iam::…")
)                                     # info.status: "active" or "pending"
info = client.get_collection("docs")  # describe one collection
cols = client.list_collections()      # every registered collection
client.verify_collection("docs")      # re-run bucket verification now
client.delete_collection("docs")      # disable permanently; bucket data is untouched
```

A pending collection activates automatically (within ~30s) once you finish
your side: write `info.claim_token` to `info.claim_path` in the bucket, or
attach the trust policy from `client.backend_setup(uri)` to the role. Without
`-byo-store` these endpoints raise `NotEnabledError`.

## Auth

```python
client = Client(
    "https://db.example.com:23000",
    api_key="plgn_<key_id>_<secret>",     # sent as Authorization: Bearer
)
```

The API key always guards the collection API on `-byo-store` servers. It also
guards every vector operation when the server starts with
`-require-data-key`. With TLS enabled server-side, use an `https://` URL
(HTTP) or pass `credentials=grpc.ssl_channel_credentials()` (gRPC); do not
send bearer keys over an untrusted plaintext connection.

## Errors

All errors subclass `polign.PolignError`:

| Exception               | HTTP | gRPC                 |
|-------------------------|------|----------------------|
| `InvalidArgumentError`  | 400  | `INVALID_ARGUMENT`   |
| `AuthenticationError`   | 401  | `UNAUTHENTICATED`    |
| `PermissionDeniedError` | 403  | `PERMISSION_DENIED`  |
| `NotFoundError`         | 404  | `NOT_FOUND`          |
| `ConflictError`         | 409  | `ALREADY_EXISTS` / admin `FAILED_PRECONDITION` |
| `NotOwnerError`         | 421  | `FAILED_PRECONDITION`|
| `RateLimitError`        | 429 (rate limit/write backlog) | `RESOURCE_EXHAUSTED` |
| `NotEnabledError`       | 501  | `UNIMPLEMENTED`     |
| `UnavailableError`      | 503  | `UNAVAILABLE`        |
| `ServerError`           | other 5xx | `INTERNAL`      |
| `ConnectionError`       | connection failure | —          |

`NotOwnerError.owner` names the owning node in fleet mode — reconnect there
and retry. gRPC uses `UNAVAILABLE` for both a server-declared degraded backend
and transport outages, so both map to `UnavailableError`; HTTP transport
failures remain `ConnectionError` because no HTTP status was received.

## Notes & caveats

Caveats shared by both transports:

- Embed documents and queries with the **same model** — distances are only
  meaningful within one embedding space.
- Auto-created collections use the server's default metric (L2) and hybrid
  IVF index; metric and index tuning are not yet exposed over the wire.
- **Bulk loads should use `put_many`** — one request per batch instead of one
  per vector. The server validates the whole batch up front (id, non-empty
  values, uniform dimension, at most 5000 vectors per batch: an invalid batch
  applies nothing); on a rarer mid-batch failure earlier vectors remain
  applied. Mutations are never retried automatically after a connection
  failure because the client cannot know whether the server applied them;
  reconcile state before deciding whether to retry. Chunk larger loads into
  batches of 5000.
- Metadata values may be strings, numbers, or booleans. Typed values compare by
  type. Cross-kind equality and range comparisons never match: for example,
  the string `"0.5"` is distinct from the number `0.5`, and numeric range
  bounds do not parse numeric-looking strings.

## License

Apache License 2.0. See [LICENSE](LICENSE).
