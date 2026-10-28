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

## Durable index documents

`IndexDocument` carries a storage-neutral `VectorPoint`, source revision, artifact
version, deletion marker and opaque auxiliary metadata. Business applications own
Card/Profile conversion; this package neither parses them nor calls models while
loading artifacts. `SqlIndexDocuments` is an optional DB-API provider scoped by
explicit `environment` and `index_key`. Every restore/change/version read is scoped.
Provision production tables externally; SQLite helpers are for local execution.

`save_in_transaction` requires the caller to own a transaction and fence the
source revision/producer lease. It does not commit or provide a distributed lock.
A business repository must atomically publish its prepared marker and document,
including when a connection uses autocommit. JSON vectors are numeric and finite;
metadata is opaque to the provider. Logical deletion retains a replayable document.

`iter_batches` restores bounded ID pages; `iter_change_batches_since` provides an
inclusive timestamp fast path. Timestamp order alone does not imply transaction
commit order. Consumers must also reconcile `iter_versions` (ID/version pages)
and fetch only mismatches to catch late commits. Never physically remove tombstones
without a separately defined consumer recovery protocol.

`core.index_sync.apply_snapshot_pages` and `apply_change_pages` contain the batch
application used by both TC and Agent Search. A change checkpoint advances only
after the complete batch succeeds. TC retains its legacy SQL, locks and schedule;
new providers do not require migrating existing TC tables.
