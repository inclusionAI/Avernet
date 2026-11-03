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
provides: [SearchApplication, RetrievalService, HybridRetrievalService, reciprocal_rank_fusion, create_search_router, create_card_router, CardManagementService, SqlCardRepository]
consumes: [RetrievalIndex, EmbeddingProvider, Reranker, CardStore, CardDocumentConverter]
internal_dependencies: []
```

- `api/`: application-facing search contracts and errors.
- `core/`: independent keyword / Embedding+dense branches, RRF and optional reranking.
- `plugin_api/`: index and model contracts, vector values.
- `providers/`: Qdrant dense/sparse execution and HTTP Embedding/reranker clients.
- `config/`: optional model configuration loader; execution receives explicit settings.
- `adapters/http/`: common v2 endpoints with an injected application and authentication.

`HybridRetrievalService` accepts a `QdrantRetrievalIndex` or another `RetrievalIndex`,
an Embedding provider, and an optional reranker. `RetrievalRequest.allowed_ids` is
trusted input from the application; an empty set returns no candidates. Filters
and authorized IDs apply before top-k. The application prepares `name`, `content`
and filter metadata from its own Profile/Card. No Worker-ID matching, TC fragment
aggregation, Fuse, Card parsing or audience policy is implicit in this pipeline.
Failures propagate as `SearchUnavailable`; fallback policy is not invented here.

`QdrantRetrievalIndex` supports Local (default) and Server through an explicit
`QdrantConnection`. Local serializes access and rebuilds its sparse index from
persisted dense payloads. Server persists both collections and permits concurrent
reads, with exclusive mutations. One application owns each collection pair;
sharing a pair across independent writers is not supported by the process-local lock.
No connection failure falls back to Local. This is not a high-QPM capacity claim.
Applications own startup, shutdown, admission limits, model quotas and update jobs.
Async orchestration keeps blocking providers off the event loop, but cancellation
cannot kill an already running sync model request; transport timeouts still apply.
See `docs/provider-contracts.md` and `docs/search-api.md` for the two API boundaries.

## Shared Card management (opt-in)

Avernet owns the v1 Card HTTP router, source envelope, partial-field merge,
server-side revisions, durable SQL writes and idempotent deletion. See the
[Card management API](docs/card-management-api.md) for the wire contract and
[provider contracts](docs/provider-contracts.md#card-management) for composition.
Install the `cards` extra for storage/domain models, and `http` for the router.

The application supplies authentication, database connections, index status and
its `CardDocumentConverter`. The converter selects text and labels from the
opaque business Card; the shared management code never interprets business
`status`, `deleted` or `version`. `catalog/` contains source domain schemas;
`core/card_management.py` contains the transport-independent service and merge;
`providers/sql_cards.py` owns public DB-API persistence.

OCB consumes this implementation and retains its Alipay projection, legacy
snapshot compatibility, index runtime and internal platform providers. TC's
existing application is unchanged: importing/installing search-core does not
mount Card routes or switch TC's existing sync/Worker/Profile flows. TC may opt
in explicitly during a later migration. No OCB dependency is required by the
public package.
