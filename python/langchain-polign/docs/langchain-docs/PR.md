docs: add Polign vector store integration

Adds the integration page for `langchain-polign`, the LangChain vector store
for Polign, plus a short provider page.

- Package: https://pypi.org/project/langchain-polign/ (Apache-2.0)
- Source and tests: https://github.com/Polign/polign/tree/main/python/langchain-polign
- Passes `langchain_tests.integration_tests.VectorStoreIntegrationTests`
  (sync and async) in CI against the latest Polign server release.

The page follows the vector store template: setup, instantiation, add,
update, delete, similarity search with and without scores, retriever,
filtering, and Polign's lexical and hybrid search methods.
