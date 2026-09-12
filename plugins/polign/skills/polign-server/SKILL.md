---
name: polign-server
description: >
  Run and operate polign_db, the object-store-native vector database.
  Use when the user is starting, configuring, deploying, or debugging a
  polign_db server: the polign-server, polign-persistor, polign-maintain,
  polign-apikey, or polign-import binaries, the -store flag and its object
  store specs (s3://, gcs://, az://, fs:), cold-first serving, the write
  log, API keys, store encryption, bucket credentials, or the admin UI.
  Also use for "polign won't start", "polign is slow", "my writes stopped",
  or questions about which flag to set.
  Do not use for writing application code against a running server; that is
  the polign-client skill.
---

# Running polign_db

polign_db keeps its data in an object store you own. The server is a static
binary with no database to install beside it, and durability comes from the
bucket rather than from the node's disk. A node can be replaced at any time
without losing data.

Do not guess flag names. Every binary prints its full flag set with
`-h`, and that output is the authority for the version the user actually has:

```sh
polign-server -h
polign-apikey -h
```

## Install

```sh
brew install polign/tap/polign          # macOS
curl -fsSL https://get.polign.com | sh  # Linux and macOS
```

Both install six binaries: `polign` (CLI), `polign-server`, `polign-persistor`,
`polign-apikey`, `polign-maintain`, and `polign-import`. To pin a version, set
`POLIGN_VERSION=vX.Y.Z` before the install script, and `POLIGN_BIN_DIR` to
choose where they land. Release tags are at
https://github.com/Polign/polign/releases.

## The one flag that matters

```sh
polign-server -store s3://your-bucket/prefix
```

`-store` is a preset, not just a location. It turns on a whole deployment:
the write log, in-process persistence, cold-first serving with tail
freshness, the heat-driven hot tier, hedged reads, a bounded local disk
cache, an in-memory segment cache, a write-backlog cap, and periodic index
maintenance. Start here and change things only when a measurement says to.

Any granular flag set explicitly overrides its derived value, so
`-store s3://... -hot-max 64` keeps the rest of the preset and changes one
piece.

Against a remote store, the preset derives:

| Setting | Value |
| --- | --- |
| `-cold-first` | true |
| `-tail-fresh` | true |
| `-hot-max` | 16 |
| `-segment-cache-bytes` | 256 MiB |
| `-hedge-reads` | 150ms |
| `-disk-cache-bytes` | 10 GiB, under the OS user cache dir |
| `-overlay-max-buffered` | 2,000,000 |

Against `fs:` the hedging and disk cache are left off, because a local
directory is already local disk. Duplicating a read of it only costs, and
caching it would hold a second copy of the same bytes.

Store specs:

```
s3://bucket/prefix
gcs://bucket/prefix
az://account/container/prefix
fs:/var/lib/polign
```

To try it with nothing to set up:

```sh
polign-server -store fs:/var/lib/polign
```

## Listeners

| Flag | Default | What it is |
| --- | --- | --- |
| `-http` | `127.0.0.1:23000` | HTTP/JSON data plane |
| `-grpc` | `127.0.0.1:23001` | gRPC data plane |
| `-admin` | off | read-only operator UI and API |

Both data listeners default to loopback. To serve other hosts, set an
explicit address such as `-http :23000`.

The admin listener has **no authentication at all**. It is a read-only
operator surface. Bind it to localhost or an operator network, never to a
public address, and never to the same address as the data plane.

Health check, always open and never authenticated:

```sh
curl -fsS localhost:23000/healthz
```

`-tls-cert` and `-tls-key` enable TLS on both data listeners. They must be
set together.

## Authentication

Off by default. Turning it on takes two steps: mint a key with bucket
credentials, then start the server with the flag.

```sh
polign-apikey -store s3://your-bucket/prefix create
polign-server -store s3://your-bucket/prefix -require-data-key
```

`-require-data-key` requires a valid bearer key on every HTTP and gRPC data
operation. Health checks stay open. Key records live in the store itself
under `.auth/`, which is why `polign-apikey` takes the same `-store` value and
bucket credentials rather than a connection to the server. A key looks like
`plgn_<key_id>_<secret>`, and the store keeps only a hash of the secret, so
`create` printing it is the one and only time the full key exists. Copy it
then or mint another.

The same verbs manage keys: `create`, `list`, `disable`, `enable`, `delete`,
the last three taking `-id <key_id>`.

`-management` serves `/v1/admin` on the HTTP listener so keys can be managed
over the API instead of with bucket credentials, authenticated by separate
admin keys:

```sh
polign-apikey -store s3://your-bucket/prefix admin create
polign-server -store s3://your-bucket/prefix -require-data-key -management
polign-apikey -api https://host:23000 -admin-key plgn_... list
```

Mint the admin key before starting the server with `-management`. Minting one
needs bucket credentials, which is the break-glass path that bootstraps the
management API in the first place.

`-management` is off by default for a reason worth repeating to anyone who
wants it on: `/v1/admin` shares the data listener, so turning it on publishes
key administration everywhere the data plane is reachable. Administering keys
with `polign-apikey` and bucket credentials is the safer default.

If `/v1/admin` returns 404, the server was started without `-management`.

## Encryption and bucket credentials

`-store-encryption-key-file` (or `POLIGN_STORE_ENCRYPTION_KEY_FILE`) points at
a keyring. With it set, every object the server writes and every disk cache
entry is ciphertext, encrypted before it leaves the process. The server logs
the write key id and keyring size at startup, which is the quickest way to
confirm it is really on.

Every node that reads a store must carry the same keyring. A node without it
cannot read objects an encrypted node wrote.

For a bucket in another account, the server assumes a role rather than using
ambient credentials:

```sh
polign-server -store s3://their-bucket/prefix \
  -store-role-arn arn:aws:iam::111122223333:role/PolignReader \
  -store-external-id <id> \
  -store-region us-west-2
```

Each has an environment variable equivalent: `POLIGN_STORE_ROLE_ARN`,
`POLIGN_STORE_EXTERNAL_ID`, `POLIGN_STORE_REGION`.

## Collections

By default collections are implicit. Writing a vector to a collection name
creates it, and there is no registry to manage. The collection endpoints
return 501, so nothing can list collection names, which matters when
configuring this plugin's MCP server: set a default collection, because
Claude cannot discover one on its own.

`-byo-store` turns on the explicit collection API and per-collection customer
backends, where each collection is bound to a customer's own bucket through
an assumed role, verified at create time with a claim token and a capability
probe. It requires `-store`, because the collection registry and the API key
records that guard it both live in the control store.

## Splitting the pieces apart

One server does everything by default. Each piece can be moved out:

| To move out | Server flag | Run instead |
| --- | --- | --- |
| Persistence | `-persist=false` | `polign-persistor` |
| Index maintenance | `-maintain 0` | `polign-maintain` |

Both are lease-guarded through the bucket, so running several replicas is
safe: one persists and the rest stand by.

Read replicas hold read-only bucket credentials and never write:

```sh
polign-server -store s3://your-bucket/prefix -read-only
```

`-read-only` never opens the write log, refuses mutations with HTTP 403 or
gRPC FailedPrecondition, and keeps persistence, maintenance, tail following,
and heat publishing off. Replicas still pick up new segment generations every
`-segment-refresh` (30s by default).

Bulk loading an existing dataset goes through `polign-import` rather than a
loop of single writes.

## Cold, warm, and hot

Cold-first serving answers queries from object storage segments, which is
what makes a small node able to serve a large index. `-cold-first=false`
switches to warm serving, where the index is rebuilt in memory from the store
at boot. Warm serving needs memory proportional to the index and pays a boot
cost, so it only makes sense for a small index that must have the lowest
possible latency.

The hot tier sits on top of cold-first: a collection whose sustained query
rate crosses 5 QPS over a trailing 10 second window is promoted into memory
from its latest published generation and kept fresh by `-tail-fresh`.
`-hot-max` bounds how many collections can be promoted at once.

Tuning cold reads, in the order worth trying:

1. `-hedge-reads` near the store's p95 GET latency. This cuts the store's p99
   tail out of cold queries and costs a few percent more GET requests.
2. `-disk-cache-dir` with `-disk-cache-bytes` on fast local NVMe. Cached
   blobs survive searcher eviction, hot demotion, and restarts.
3. `-segment-cache-bytes`. Concurrent queries touching the same postings,
   cells, or metadata share one resident copy instead of each reading its
   own. This is what bounds a small node's memory under concurrent
   keyword-search load.
4. `nprobe` per query rather than a server flag. On a cold IVF-PQ read, how
   many cells get probed is the main lever on recall, more so than the
   codebook.

## Things that go wrong

**Writes to one collection stopped, others are fine.** Look for a repeating
`write log append: objectlog: list partition N: ... context deadline
exceeded`. A collection maps to exactly one write log partition, and append
finds the next sequence number by listing that partition, so the cost grows
with every batch ever written to it. Once that listing outgrows its timeout,
the partition is permanently unwritable while every other partition keeps
working. Check the object count with
`aws s3 ls s3://<bucket>/<prefix>/.wal/<N>/ --recursive | wc -l`.
The fix is retention: `-log-retain-batches` keeps the newest N batch objects
per partition and deletes what every consumer group has committed past.
Deletion is irreversible, so do not set it smaller than the margin a stalled
consumer might need.

**Writes are rejected with 429 or RESOURCE_EXHAUSTED.** The buffered write cap
is doing its job: unpersisted records hit `-overlay-max-buffered` and new ids
are refused before anything is logged, which bounds node memory when the
persistor is down or behind. Updates and deletes of already-buffered ids still
work. Retry after the persistor catches up rather than raising the cap, and
check whether the persistor is running at all.

**Boot takes many minutes on a large store.** Replaying several thousand
segments is normal and has nothing to do with write log size.

**The server refuses to start over a node id.** `-node-id` and every `-peers`
entry must be ASCII, space free, and at most 128 characters.

**Nothing writes and nothing errors in an obvious way on S3, GCS, or Azure.**
Check the binary is the full build. A build without the cloud backends
compiled in is around 20MB against around 65MB for the full one.

## Fleets

`-node-id` on its own only names the node in heat records. Paired with
`-peers`, it turns on placement: cold reads for a collection this node does
not own are redirected with HTTP 421 or gRPC FailedPrecondition. Start every
node with the same `-peers` set; a node's own id is added automatically.

`-rate-limit` caps requests per second across both transports, with bursts up
to twice the rate.
