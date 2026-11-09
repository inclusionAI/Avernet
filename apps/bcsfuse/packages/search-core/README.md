# Search Core

Independently installable public package owned by the BCSFuse module. Both TC and
Agent Search reuse its contracts, retrieval and model clients; business rules stay
in the applications. No database, model connection or task starts on import.

Install `uv pip install -e 'apps/bcsfuse/packages/search-core[http,models,qdrant,test]'`.
Run `python -m pytest tests` in this package. OCB must build it from the same
Avernet revision as the public TC source, rather than fetching a floating branch.

## Context Boundary

```yaml
purpose: Shared search execution and optional Card management and delivery/provider adapters.
provides: [create_catalog_search_app, DefaultCardDocumentConverter, CardUpdates, SqlCatalogRepository, SearchApplication, RetrievalService, HybridRetrievalService, reciprocal_rank_fusion, create_search_router, create_card_router, CardManagementService, SqlCardRepository, IndexBuildService, rebuild_source]
consumes: [RetrievalIndex, EmbeddingProvider, Reranker, CardStore, CardDocumentConverter, IndexBuildSource, IndexBuildRepository]
internal_dependencies: []
```

- `api/`: application-facing search contracts and errors.
- `application/`: optional catalog runtime: admission, cache, fallback and metadata lookup.
- `catalog/`: default conversion, Doc materialization and replica synchronization.
- `bootstrap/`: explicit composition and standalone local HTTP entrypoint.
- `core/`: independent keyword / Embedding+dense branches, RRF and optional reranking.
- `plugin_api/`: index and model contracts, vector values.
- `providers/`: Qdrant dense/sparse execution and HTTP Embedding/reranker clients.
- `config/`: optional model configuration loader; execution receives explicit settings.
- `adapters/http/`: common v2 endpoints with an injected application and authentication.

`EmbeddingSettings.request_dimensions` defaults to `False` for existing providers.
Hosts selecting a model with configurable output dimensions can set it to `True`;
both sync preparation and async query clients then send the configured `dimensions`
to the model API. The selected model must support this parameter.

Index generation management, single-source repair and resumable build execution
are shared capabilities. Business source adapters supply conversion and guarded
publication; internal data-source clients and dynamic configuration stay in the
host. See [the rebuild contract](docs/index-rebuild.md). Importing the package
does not enable build jobs or change TC behavior.

`HybridRetrievalService` accepts a `QdrantRetrievalIndex` or another `RetrievalIndex`,
an Embedding provider, and an optional reranker. Business filters apply before top-k. The application prepares `name`, `content`
and filter metadata from its own Profile/Card. No Worker-ID matching, TC fragment
aggregation, Fuse, Card parsing or audience policy is implicit in this pipeline.
Failures propagate as `SearchUnavailable`; fallback policy is not invented here.

`QdrantRetrievalIndex` supports Local (default) and Server through an explicit
`QdrantConnection`. Local serializes access and rebuilds its sparse index from
persisted dense payloads. Server persists both collections and permits concurrent
reads, with exclusive mutations. One application owns each collection pair;
sharing a pair across independent writers is not supported by the process-local lock.
No connection failure falls back to Local. This is not a high-QPM capacity claim.
Hosts select startup, shutdown and runtime policy. The optional catalog composition
provides shared admission, cache, fallback and background index updates; TC keeps
its existing orchestration.
Async orchestration keeps blocking providers off the event loop, but cancellation
cannot kill an already running sync model request; transport timeouts still apply.
See `docs/provider-contracts.md` and `docs/search-api.md` for the two API boundaries.

## Shared Card management (opt-in)

Avernet owns the v1 Card HTTP router, source envelope, partial-field merge,
server-side revisions, durable SQL writes and idempotent deletion. See the
[Card management API](docs/card-management-api.md) for the wire contract and
[provider contracts](docs/provider-contracts.md#card-management) for composition.
Install the `cards` extra for storage/domain models, and `http` for the router.

The application supplies authentication and database/model/index connections.
`create_catalog_search_app` composes shared preparation, replica synchronization,
cache invalidation, retrieval and metadata enrichment. It uses
`DefaultCardDocumentConverter` unless an application supplies its own converter. The converter selects text and labels from the
opaque business Card; the shared management code never interprets business
`status`, `deleted` or `version`. `catalog/` contains source domain schemas, conversion and synchronization;
`core/card_management.py` contains the transport-independent service and merge;
`providers/sql_cards.py` owns public DB-API persistence.

OCB consumes this implementation and retains its Alipay projection, legacy
snapshot compatibility, operational rules and internal platform providers. The
shared SQL publication, synchronization, cache and Qdrant process lifecycle are
implemented here; OCB compatibility imports delegate to this package. TC's
existing application is unchanged: importing/installing search-core does not
mount Card routes or switch TC's existing sync/Worker/Profile flows. TC may opt
in explicitly during a later migration. No OCB dependency is required by the
public package.

## Standalone local application

No OCB source or internal platform account is required. Install the `http,qdrant`
extras and run `bcsfuse-catalog --data-dir .data/catalog --port 8877`. It binds to
loopback, stores Card/Doc records in SQLite and uses Qdrant Local by default.
The bundled deterministic demo vectors are for functional validation, not model
quality or performance evaluation. See [the local guide](docs/local-catalog.md)
for installation, write/search/update/delete requests and custom conversion.
