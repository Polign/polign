# Listing langchain-polign on docs.langchain.com

LangChain's integration docs live in the
[langchain-ai/docs](https://github.com/langchain-ai/docs) repository. The
vector store table on the integrations page is generated from each page's
frontmatter, so listing the package means adding one page (plus an optional
provider page). Nothing in `docs.json` needs to change: the navigation lists
only the vector store index, and the table links to each page.

## Files

| File here | Destination in langchain-ai/docs |
|---|---|
| `polign.mdx` | `src/oss/python/integrations/vectorstores/polign.mdx` |
| `providers-polign.mdx` | `src/oss/python/integrations/providers/polign.mdx` |

## Steps

1. Fork `langchain-ai/docs` and clone the fork.
2. Copy the two files to the destinations above.
3. Optional: `make lint` in the docs checkout runs their markdown checks.
4. Open a PR with the title `docs: add Polign vector store integration` and
   the body from `PR.md`.

The download and featured tables under `src/snippets/oss/` are regenerated
by the maintainers with `scripts/refresh_integration_downloads.py`; do not
edit them in the PR.

Keep the frontmatter `integration:` block accurate when the package changes.
`passes_standard_tests: true` is backed by `tests/integration_tests`, which
runs `langchain_tests.integration_tests.VectorStoreIntegrationTests`.
