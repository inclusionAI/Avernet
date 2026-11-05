# Retrieval provider contracts (v0.1)

Service API: `RetrievalService.retrieve(RetrievalRequest) -> SearchResult`.
Plugin API: `RetrievalIndex`, `EmbeddingProvider`, `Reranker`. Business applications
construct these providers at bootstrap.

| Provider | Input and output | Required behavior |
| --- | --- | --- |
| RetrievalIndex | query/vector, limit, attribute filters → ranked hits | Different filter fields AND; values within a field OR. Filter before top-k. An empty list of values for a filter matches nothing. IDs remain opaque. |
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
version, deletion marker and opaque auxiliary metadata. Business applications can supply Card/Profile conversion. The optional default
converter selects documented semantic fields; recovery always consumes saved
artifacts without calling converters or models. `SqlIndexDocuments` is an optional DB-API provider scoped by
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

`load_vector_candidates(source_id, embedding_key)` is the explicit exception to
generation-scoped reads: it returns nondeleted candidates across generations in
the same environment, newest first, for embedding reuse only. Doc IDs may repeat.
Consumers rerun conversion and match text hashes, model/dimension and valid vectors;
they must never restore or serve these candidates directly. Contract coverage is
in `tests/test_index_documents.py`; current source revisions still fence publication.

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
  SQL source deletion uses a dedicated `is_deleted` column and never rewrites
  `card_json`. Catalog composition tombstones all Docs for the source across
  index keys in the same source transaction, bumping artifact versions/timestamps.
  Source-only composition has no Doc table to update. Leases and source revisions
  fence in-flight producers; a failed Doc update rolls back source deletion.
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
- `CardRecord.preparation_key` carries the existing source-row error owner. Status
  exposes a source preparation error only when it belongs to the responding
  instance's index key; other generation failures are queried through build status.
- HTTP Card writes accept only `card` and optional `resource_type`; reads return
  the complete merged business object and system revision. Optional internal
  operations settings are not accepted as an HTTP `search_config` field.
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

## Optional catalog runtime

`bootstrap.catalog.create_catalog_search_app` explicitly assembles the public
preparation/synchronization and query flow. `catalog.updates.CardUpdates` owns
leases, source-revision checks, restoration, incremental reads and reconciliation;
`SqlCatalogRepository` publishes Doc groups and their source marker atomically.
The host supplies DB connections, model/index providers and authentication.
`application` holds optional Pydantic-backed policy models; `core` and `api`
remain framework-independent. No background activity starts at import time.

`DefaultCardDocumentConverter` returns one `CardProjection` containing `main`.
Applications replace it through the existing `CardDocumentConverter` contract:
`convert(snapshot)` returns text documents and explicitly allowed filter labels.
The runtime embeds/reuses vectors, publishes the complete group, retires removed
Docs, and groups retrieval by source ID. Converter `version` participates in the
configuration key; changing conversion behavior requires a new version or an
explicit repair/rebuild. OCB retains its historical version/key calculation.

The catalog factory accepts application-specific update, metadata and HTTP factories
for hosts that need legacy Card decoding, operational state or a different response
projection. These are ordinary injected callables, not a plugin registry.
`QdrantProcessProvider` and `VectorIndexLifecycleProvider` are public optional
providers; binary paths, process environment, schedules and internal SDK clients
remain the host's responsibility. Install the `scheduler` extra for the latter.

## Optional index statistics

`IndexStatistics.point_counts()` is a Plugin API capability implemented by
`QdrantRetrievalIndex` for Local and Server. Without an argument it returns
`vector_points` and `keyword_points`. With `point_counts(index_key)` it additionally
returns `vector_matching_points`, `vector_mismatched_points`,
`keyword_matching_points` and `keyword_mismatched_points`. The provider counts
payload matches and their complement using Qdrant exact filtered counts; totals
are their sums. Missing or different `index_key` values count as mismatched. It does not enumerate IDs or fetch vectors, and does not sum old
versions or other replicas. Errors propagate; unavailable storage is never zero.
The counts are observations, not a distributed snapshot or source Card count.
Existing `RetrievalIndex` implementations do not have to provide this capability.

The catalog HTTP composition requests it only for `/ready?include_counts=true`,
passing the effective instance index key through its bounded probe executor. Default readiness checks remain unchanged.
Provider conformance is exercised by `tests/test_qdrant_modes.py`; HTTP behavior
and normal readiness under count failures by `tests/test_public_catalog.py`.
