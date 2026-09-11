# Listing langchain-polign on docs.langchain.com

LangChain no longer accepts manual docs PRs for new listings. The process
(see docs.langchain.com, "Publish an integration") is:

1. File an **Integration listing** issue in `langchain-ai/docs` using the
   form at
   https://github.com/langchain-ai/docs/issues/new?template=06-integration-submission.yml
2. A maintainer applies the `integration-run` label. Automation then opens
   a PR that adds a row to `scripts/data/integration_external_docs.yaml`
   and regenerates the vector store table and provider card. The name
   column links to the `docs_url` from the form.
3. Packages under 50,000 monthly downloads get that external row only.
   A hosted guide page (`polign.mdx` here) is added only once the package
   passes 50,000 monthly downloads or a maintainer marks it featured.

## Form values

| Field | Value |
|---|---|
| Display or class name | `PolignVectorStore` |
| Language | Python |
| Component type | vectorstores |
| PyPI package name | `langchain-polign` |
| Docs URL | `https://github.com/Polign/polign/tree/main/python/langchain-polign` (switch to the polign.com guide once it exists) |
| Source repository | `Polign/polign` |
| Short provider description | Vector store for Polign, an object-store-native vector database, with metadata filtering, MMR, and BM25 hybrid search. |
| Capability flags | see below |

```
delete_by_id: true
filtering: true
search_by_vector: true
search_with_score: true
async_api: true
passes_standard_tests: true
multi_tenancy: false
ids_in_add_documents: true
```

## Files kept here

| File | Purpose |
|---|---|
| `polign.mdx` | Hosted guide page, ready for `src/oss/python/integrations/vectorstores/polign.mdx` when eligible. Every snippet was run against a live server. |
| `providers-polign.mdx` | Provider page to go with it, `src/oss/python/integrations/providers/polign.mdx`. |
| `PR.md` | PR title and body for the hosted-guide submission. |

Keep the capability flags in sync with `polign.mdx` frontmatter and the
package. `passes_standard_tests: true` is backed by `tests/integration_tests`,
which runs `langchain_tests.integration_tests.VectorStoreIntegrationTests`.
