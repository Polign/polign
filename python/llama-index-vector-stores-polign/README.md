# llama-index-vector-stores-polign

LlamaIndex vector store for [polign_db](https://polign.com).

```bash
pip install llama-index-vector-stores-polign
```

## Usage

```python
from llama_index.core import StorageContext, VectorStoreIndex
from llama_index.vector_stores.polign import PolignVectorStore

store = PolignVectorStore(
    collection_name="docs",
    url="http://localhost:23000",   # api_key="plgn_..." when the server requires one
)
storage_context = StorageContext.from_defaults(vector_store=store)
index = VectorStoreIndex.from_documents(documents, storage_context=storage_context)

retriever = index.as_retriever(similarity_top_k=5)
retriever.retrieve("what purrs?")

index.delete_ref_doc("document-id")          # removes every chunk of that document

# reopen later without re-indexing
index = VectorStoreIndex.from_vector_store(store)
```

Collections are created on the first write and take their dimension from
the first embedding. Pass `client=` with an existing `polign.Client` or
`polign.GrpcClient` to reuse a connection or use the gRPC transport
(`pip install "llama-index-vector-stores-polign[grpc]"`).

## Metadata filters

`MetadataFilters` translate to polign's filter language. Supported
operators: `EQ`, `NE`, `GT`, `GTE`, `LT`, `LTE`, `IN`, `NIN`, `ANY`, `ALL`,
`CONTAINS`, `IS_EMPTY`, with `AND`, `OR`, and `NOT` conditions and nesting.
`TEXT_MATCH` is not supported. `query.doc_ids` and `query.node_ids` are
honored.

```python
from llama_index.core.vector_stores.types import MetadataFilter, MetadataFilters, FilterOperator

retriever = index.as_retriever(
    similarity_top_k=5,
    filters=MetadataFilters(filters=[
        MetadataFilter(key="lang", value="en"),
        MetadataFilter(key="year", value=2024, operator=FilterOperator.GTE),
    ]),
)
```

## Query modes

| Mode | What runs |
|---|---|
| `DEFAULT` | vector search; similarity is `1 / (1 + d)` for L2 collections, `1 - d` for cosine |
| `TEXT_SEARCH`, `SPARSE` | BM25 over the node text; similarity is the BM25 score |
| `HYBRID` | vector plus BM25 fused server-side; linear with `alpha` when set, reciprocal rank fusion otherwise |
| `MMR` | re-ranks the `similarity_top_k * mmr_prefetch_factor` nearest nodes with `mmr_threshold` |

Pass `similarity_fn=` to the constructor to change the distance mapping.
`ef`, `cold`, `nprobe`, and `rescore` given as extra query kwargs go to
`polign.Client.search` unchanged.

Text and hybrid modes need a server with a segment store
(`polign-server -store ...`); an in-memory server raises
`polign.InvalidArgumentError`. The BM25 index is built when the server
persists a segment, so nodes take part in lexical search only after the
next segment is written and the searchers refresh, about half a minute with
default settings. Vector search sees writes immediately.

## How nodes are stored

- The polign record id is the node id, also stored under `_node_id`.
- The node text lives in the metadata key `text`, the field the server's
  BM25 index reads by default. Change it with `text_key=` only if the server
  is configured for another field.
- Everything else is what LlamaIndex's `node_to_metadata_dict` produces: the
  node as JSON under `_node_content`, `_node_type`, `ref_doc_id` (also as
  `doc_id` and `document_id`), and the node's metadata at the top level so
  it can be filtered on. Top-level values polign cannot store (nested
  objects, `None`) are JSON-encoded there; the node itself is unaffected.

## Limits

- `clear()` deletes every record; dropping the collection itself needs the
  server's `-byo-store` collection API.
- Async methods run the synchronous client in a worker thread.
- Writes are sent in batches of up to 5,000 records, the server's limit.

## Development

```bash
pip install -e ".[grpc]" pytest pytest-asyncio
pytest                      # boots a polign-server; see tests/conftest.py
```

## License

Apache License 2.0. See [LICENSE](LICENSE).
