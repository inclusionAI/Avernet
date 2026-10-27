# Search Core

Independently installable public package owned by the BCSFuse module. Both TC and
Agent Search reuse its contracts, retrieval and model clients; business rules stay
in the applications. No database, model connection or task starts on import.

Install `uv pip install -e 'apps/bcsfuse/packages/search-core[http,models,qdrant,test]'`.
Run `python -m pytest tests` in this package. OCB must build it from the same
Avernet revision as the public TC source, rather than fetching a floating branch.

## Context Boundary

```yaml
purpose: Shared search execution and optional delivery/provider adapters.
provides: [SearchApplication, RetrievalService, HybridRetrievalService, reciprocal_rank_fusion, create_search_router]
consumes: [RetrievalIndex, EmbeddingProvider, Reranker]
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

The current index is Qdrant **Local**, with serialized index access. Its persisted
payloads rebuild the disposable sparse index on startup. It is a reusable local
implementation, not a validated Alipay high-QPM deployment or Qdrant Server adapter.
Applications own startup, shutdown, admission limits, model quotas and update jobs.
Async orchestration keeps blocking providers off the event loop, but cancellation
cannot kill an already running sync model request; transport timeouts still apply.
See `docs/provider-contracts.md` and `docs/search-api.md` for the two API boundaries.
