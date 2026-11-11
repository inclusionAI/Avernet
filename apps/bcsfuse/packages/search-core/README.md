# Search Core

Independently installable public package owned by the BCSFuse module. Both TC and
Agent Search reuse its contracts, retrieval and model clients; business rules stay
in the applications. No database, model connection or task starts on import.

Install `uv pip install -e 'apps/bcsfuse/packages/search-core[http,models,qdrant,test]'`.
Run retrieval tests from `apps/bcsfuse` using the Server setup below. OCB must build it from the same
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
- `providers/`: Qdrant dense/native BM25 execution and HTTP Embedding/reranker clients.
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

`QdrantRetrievalIndex` requires Qdrant Server >=1.20 through an explicit
`QdrantConnection`. Dense and native BM25 collections share that Server.
Python Local cannot execute multilingual BM25 and is rejected, without fallback.
Server allows concurrent reads; writes remain exclusive. Both HTTP and gRPC
run the same provider contracts. Dense-only Local adapters remain available.

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
extras and run `bcsfuse-catalog --data-dir .data/catalog --port 8877
--qdrant-url http://127.0.0.1:6333`. It binds to loopback, stores Card/Doc records
in SQLite and connects to your local Qdrant Server.
The bundled deterministic demo vectors are for functional validation, not model
quality or performance evaluation. See [the local guide](docs/local-catalog.md)
for installation, write/search/update/delete requests and custom conversion.

## Native BM25 keyword retrieval

Qdrant Server 1.20 owns token frequencies, document frequencies, length
normalization and BM25 scoring (server defaults k1=1.2, b=0.75). Name and content
use its native multilingual tokenizer. Whoosh's standard RegexTokenizer,
LowercaseFilter and NgramFilter produce fixed three-character name tokens in a
separate whitespace-tokenized BM25 field. Whoosh does not store or score an index.

Each field is queried with the same filters. Qdrant's native MaxExpression takes
the best field score, analogous to Elasticsearch best_fields with tie_breaker=0.
There are no custom field weights, term boosts, synonym mappings, or ASCII query
branches. Name fragments can also match unrelated names; this does not imply
semantic equivalence or identical ES ranking (tokenizers/corpus statistics differ).
The existing dense recall, RRF fusion and optional reranker remain unchanged.

Only the primary Card Doc carries `name`, so a multi-Doc Card does not duplicate
name matches across every fragment. Secondary Docs retain their own search text.

Deploy all writers consistently. Existing startup recovery republishes durable
Docs into the new BM25 payload fields; old sparse vectors are no longer queried.
Use the existing Card rebuild operation with the current `index_key` to populate
`name` in older Docs. No source table deletion, new index key or embedding-model
change is needed. Wait for Doc/index readiness and verify both recall branches.
Do not mix old sparse-only writers with the new native BM25 readers.

Retrieval tests require an isolated Qdrant Server >=1.20:

```bash
# From apps/bcsfuse; the test suite deletes its newly created collections.
QDRANT_TEST_URL=http://127.0.0.1:6333 QDRANT_TEST_GRPC_PORT=6334 \
  python -m pytest -c pyproject.toml packages/search-core/tests tests/contract -q
```

### Card metadata cache

Card metadata cache invalidation is scoped to changed Card IDs and cached
entries that declare those IDs as dependencies. Search result and default
candidate caches still invalidate globally because updates may change ranking
or eligibility. Legacy unscoped mutation calls continue to clear all metadata.

On natural metadata expiry, one request refreshes the shared load while followers
may use the retained value for up to `min(5000, ttl_ms)` additional milliseconds.
Reads and failed refreshes do not extend that deadline. Explicit invalidation
removes retained values immediately, and revision checks prevent late old loads
from repopulating the cache. Empty or hard-expired entries wait for the shared
load. This coordination is process-local; retained values and dependencies count
toward the existing capacity limits. Other cache types keep strict TTL expiry.
