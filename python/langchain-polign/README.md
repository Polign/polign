# langchain-polign

LangChain vector store for [polign_db](https://polign.com).

```bash
pip install langchain-polign
```

## Usage

```python
from langchain_openai import OpenAIEmbeddings
from langchain_polign import PolignVectorStore

store = PolignVectorStore(
    embedding=OpenAIEmbeddings(),
    collection="docs",
    url="http://localhost:23000",   # api_key="plgn_..." when the server requires one
)

store.add_texts(
    ["cats purr", "dogs bark"],
    metadatas=[{"lang": "en", "score": 0.9}, {"lang": "en", "score": 0.4}],
)

store.similarity_search("purring", k=1, filter={"score": {"$gte": 0.5}})
store.similarity_search_with_relevance_scores("barking", k=2)
store.max_marginal_relevance_search("animals", k=2, fetch_k=10)
store.get_by_ids(["..."])
store.delete(["..."])
store.delete(filter={"lang": "fr"})

retriever = store.as_retriever(search_kwargs={"k": 4, "filter": {"lang": "en"}})
```

Collections are created on the first write and take their dimension from the
first vector. `filter` is polign's metadata predicate language: a plain
mapping is equality ANDed across keys; `$eq`, `$ne`, `$in`, `$gt`, `$gte`,
`$lt`, `$lte`, `$exists` and the composers `$and`, `$or`, `$not` build richer
predicates.

Pass `client=` with an existing `polign.Client` or `polign.GrpcClient` to
reuse a connection or to use the gRPC transport (`pip install
"langchain-polign[grpc]"`).

## How documents are stored

- The polign record id is the document id.
- `page_content` is stored in the metadata key `text`. That is the field the
  server's BM25 index reads, so hybrid search needs no extra setup. Change it
  with `text_key=` only if the server is configured for another field.
- Every other metadata entry is stored as polign metadata. Strings, numbers,
  booleans, and flat lists of those pass through unchanged and can be
  filtered on. Nested objects, `None`, and lists of objects are JSON-encoded
  as strings and their keys are recorded in the reserved `_lc_json_keys`
  entry, so reads restore the original value. Those keys cannot be filtered on.
- `text` and `_lc_json_keys` are reserved; using them in metadata raises
  `ValueError`.

## Scores

`similarity_search_with_score` returns the collection distance, smaller is
closer. `similarity_search_with_relevance_scores` maps it to `[0, 1]` using
the metric the server reports: `1 - d` for cosine, `1 / (1 + d)` for L2.
Pass `relevance_score_fn=` to the constructor to use your own mapping.

## Lexical and hybrid search

```python
store.lexical_search("brown fox", k=5)            # BM25 only
store.hybrid_search("brown fox", k=5, alpha=0.6)  # vector + BM25, linear fusion
store.hybrid_search("brown fox", k=5)             # reciprocal rank fusion
```

Both return `(document, score)` with larger meaning better. They need a
server with a segment store (`polign-server -store ...`); an in-memory server
raises `polign.InvalidArgumentError`.

The BM25 index is built when the server persists a segment, so records are
lexically searchable only after the next segment is written and the cold
searchers pick it up (`-segment-refresh`, 30 seconds by default). Expect
roughly half a minute between a write and its first lexical or hybrid hit
with default settings; vector search sees the write immediately.

## Limits

- Deleting a whole collection needs the server's `-byo-store` collection API;
  `delete(filter=...)` removes matching records instead.
- MMR reads the candidates' vectors back with one batch call, because search
  hits do not carry vectors.
- Writes are sent in batches of 5,000 records, the server's limit.

## Development

```bash
pip install -e ".[grpc]" pytest langchain-tests
pytest tests/unit_tests
pytest tests/integration_tests      # boots a polign-server; see tests/integration_tests/conftest.py
```

## License

Apache License 2.0. See [LICENSE](LICENSE).
