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

For new MySQL-compatible deployments, `gmt_create` and `gmt_modify` are
`TIMESTAMP(6)`, with database initialization and automatic modification time.
The SQL adapter projects modification time through `UNIX_TIMESTAMP` and binds
write/range values through `FROM_UNIXTIME`; the service-facing cursor remains
epoch seconds. Range predicates keep the indexed column unwrapped. SQLite retains
its existing numeric schema. Deploy this adapter with its matching timestamp DDL;
existing numeric MySQL columns require an explicit data migration, not a direct
type change or merely rerunning `CREATE TABLE IF NOT EXISTS`.

`IndexDocument.filters` stores structured pre-filter labels separately from its
text/association payload. Applications choose the allowed fields during conversion;
the SQL provider only persists them. `to_index_point(filter_fields)` projects these
labels into the engine payload used by both dense and keyword retrieval. Configured
filter fields are removed from legacy payload before applying explicit filter values.
`None` / SQL NULL preserves legacy payload labels; `{}` means explicitly no labels.
Neither recovery nor filter-only changes require generating new vectors.

SQLite initialization adds the nullable column to existing local databases. MySQL
tables must receive the nullable `filters JSON` column before deploying the adapter.
Existing rows need no bulk rewrite. Applications must supply their legacy filter
field names when projecting explicitly cleared labels. TC's legacy provider, schema
and behavior are unchanged.

## Card management

- Service API: `search_core.api.cards.CardManagement`, consumed by
  `search_core.adapters.http.cards.create_card_router`.
- Storage Plugin API: `search_core.plugin_api.card_store.CardStore`, consumed by
  `search_core.core.card_management.CardManagementService`. `read_card` must not
  load vectors; `upsert` merges provided fields and commits before returning;
  `remove` is idempotent. Failures propagate. Versions remain internal.
- Builder Plugin API: `search_core.plugin_api.card_documents.CardDocumentConverter`
  consumes an opaque source `CardSnapshot` and returns `CardProjection` (documents
  plus filter labels). Composition owns converter choice and version. Management
  never invokes the converter to decide whether a Card should exist.
- Public adapter: `search_core.providers.sql_cards.SqlCardRepository` accepts a
  DB-API connection factory; SQLite and MySQL placeholders are supported. It
  does not obtain secrets or initialize production schema on startup. The host
  owns connection and background-task lifecycle. `sqlite_card_repository(path)`
  initializes local source tables transactionally, without external services.
- Internal storage envelope: `search_core.catalog.cards.CardSnapshot` contains
  identity, opaque `source_card`, internal revision token, deletion marker and
  separate optional operations settings. Business JSON is not normalized. Hosts
  with historical normalized records can subclass the snapshot and override the
  repository decoder; public storage/write semantics remain shared.
- `CardRecord.prepared` is an optional host artifact-cache value, carried through
  internal writes for compatibility. Public CRUD does not read an artifact table.
- Conformance: `tests/test_card_management.py` exercises the actual public router,
  service and SQLite adapter (including failures), with no OCB imports. The import
  boundary suite prevents public dependencies on either application. OCB runs its
  existing builder/index integration tests against the same contracts.

Affected consumer: OCB's Card management. No new routes or storage are selected
for TC; its existing startup remains unchanged. The wire paths, status codes,
partial-update behavior and SQL table layout are preserved. Public and host
packages must be released together at compatible revisions; extraction alone
is not deployment evidence.
