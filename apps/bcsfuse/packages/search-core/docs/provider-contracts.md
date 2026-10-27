# Retrieval provider contracts (v0.1)

Service API: `RetrievalService.retrieve(RetrievalRequest) -> SearchResult`.
Plugin API: `RetrievalIndex`, `EmbeddingProvider`, `Reranker`. Business applications
construct these providers at bootstrap and supply trusted authorized item IDs.

| Provider | Input and output | Required behavior |
| --- | --- | --- |
| RetrievalIndex | query/vector, limit, attribute filters, allowed IDs → ranked hits | Different filter fields AND; values within a field OR. Filter before top-k. `None` scope is unrestricted; empty scope matches nothing. IDs remain opaque. |
| EmbeddingProvider | text → vector | Dimension/model must match the stored index. HTTP/status/parse failures remain errors. Explicit configuration supplied by composition. |
| Reranker | query, candidate IDs and text, top-k → scored IDs | Preserve candidate identity; finite model scores. No business profile lookup or LLM answer synthesis. |

The default orchestration starts lexical recall independently of Embedding, fuses
ranked IDs with RRF, and optionally reranks the bounded candidate set. Index/model
failures raise `SearchUnavailable`; they are distinguishable from a successful
query with no matches. Model-specific scores are not interchangeable with RRF.

TC retains its own fragment aggregation and Worker/Profile policy. Its thin
adapters reexport shared value classes, load existing env configuration, preserve
TC diagnostics, and explicitly enable Worker-ID keyword priority. Generic sparse
retrieval does no such ID lookup. Provider conformance coverage lives in
`tests/test_provider_execution.py` and `tests/test_retrieval_pipeline.py`; TC and
OCB additionally run their existing compatibility/composition suites.
