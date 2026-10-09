# BCSFuse Application Composition Contract

BCSFuse has one public source owner for shared business behavior: Avernet. A
deployment assembles environment-specific providers outside that shared code
and passes the completed registry into the public application factory.

## Public composition API

The public boundary consists of these Python APIs:

```python
ApplicationContext(
    *,
    mode: str,
    startup_profile: str,
    registry: ProviderRegistry,
)

create_bcsfuse_app(context: ApplicationContext) -> FastAPI
```

`ApplicationContext` is an explicit, already-assembled dependency container.
The factory does not select a deployment, import an internal implementation,
or read internal configuration in order to complete the provider graph.

`create_opensource_app(mode)` remains the compatibility entry point for the
public deployment. It builds the public provider registry and delegates to
`create_bcsfuse_app`.

## Runtime business logging

The composed app configures the shared `src` business logger at construction
and reapplies its level after provider startup. `LOG_LEVEL` explicitly overrides
the default: `DEBUG` for `prepub`/`pre`, `INFO` otherwise (including production
and local startup). Environment selection follows `SERVER_ENV`, then
`REAL_SERVER_ENV`, then `ALIPAY_APP_ENV`, using the first nonempty value.
Accepted levels are `DEBUG`, `INFO`, `WARNING`, `ERROR`, and `CRITICAL`;
invalid values fail application construction rather than silently falling back.

Set `LOG_LEVEL` in the **running application's environment**, not just in an
image-build job. Restart the target instances to apply a change; there is no
hot-reload endpoint. At INFO/DEBUG, startup logs `business_log_level` so operators
can verify the selected level. Host handlers, formatters, filters and file
routing remain intact; their own filtering still applies. Third-party logger
levels are not raised to DEBUG. A public process without host handlers gets a
console handler with request trace IDs.

INFO retrieval logs include stage counts, thresholds, timing and bounded batches
of candidate decisions (IDs, scores, ranks, admission/removal reasons). DEBUG adds
raw fragment hit details. These diagnostics never log query text, profile
contents, vectors or credentials.
Dependency construction, Qdrant adapter details and embedding request setup are
shown only when `LOG_LEVEL=DEBUG`; their WARNING/ERROR records remain visible at
INFO. HTTP client wire chatter stays at WARNING even in business DEBUG mode.
Initialization logs describe defaults, not the current request's rerank state.
Use `candidate_selection.rerank_enabled` for the effective request decision and
the subsequent `reranker` input/output/degradation counts for execution evidence.
Candidate selection is logged before reranking. `candidate_decisions` has an
explicit `fields` array and at most 25 compact `entries` per INFO record, with no
silent truncation. `max_rank` and `weighted_rank` rank eligible profiles, not
fragments. `hit_types` identifies the fragment types actually recalled.
`max_head`, `weighted_head`, `both_heads`, `max_refill`, and `weighted_refill`
indicate admission; `budget_rejected` means the profile was recalled but did not
fit the rerank budget. `fragment_type_removed`, `candidate_excluded`, and
`candidate_metadata_removed` explain earlier filtering. `reranker_returned`,
`reranker_not_returned`, `result_build_removed`, `registry_removed`,
`before_threshold`, and `threshold_removed` identify later stages at INFO.
Not being returned by the reranker is not proof of a particular model score:
the model can return only its top K and existing degradation policy also applies.
`reranker_returned.score_source` distinguishes model scores from
`aggregate_fallback`; `reranker_not_returned` lists the original weighted score,
not an inferred model score.
A profile absent from both candidate decisions and removal records did not reach
the eligible pool; these logs alone cannot distinguish missing index data,
payload prefilter rejection, or a fragment outside the initial search limit.

### Bounded fragment rerank candidate selection (HTTP v1 compatible)

The existing fragment search limit, eligibility filters, aggregation formula,
weights, rerank model input text and thresholds are unchanged. For rerank budget
`N = topK * expand_factor`, select the top `ceil(N/2)` eligible profiles by
highest **raw** fragment similarity and top `floor(N/2)` by existing aggregate
score. Deduplicate by complete `profile_key`, then alternate through the max
and aggregate tails, skipping already selected keys, until N unique profiles
or all available profiles are selected. Ties use ascending `profile_key`.
For topK=10 and expand_factor=10, the initial quotas are 50/50, not 100/100.
This dense selection operates only within the initially recalled fragment pool,
not the whole database. Keyword recall is a separate route described below.

Selected candidates are supplied in aggregate-score order to preserve existing
no-provider/error fallback ordering when there are no keyword matches.
Explicitly disabling rerank without keyword matches retains aggregate-only
selection and ranking. Storage schemas and durable vectors are unchanged.

### Keyword recall and score semantics (HTTP v1 compatible)

The durable Qdrant vector-store composition implements the existing
`VectorStoreAdapter.text_search` / `batch_text_search` plugin methods with a
derived, in-process Qdrant sparse collection. Rebuild, upsert, payload update,
deletion and incremental tombstones update this collection with the dense index.
It is rebuilt from durable payloads on startup; no new database table or historical
embedding rewrite is required. Other providers may return no keyword matches.
An unavailable lexical index is explicitly logged and falls back to dense recall;
persistence write failures continue to propagate rather than returning success.

Text comes from `content`, then `searchable_text`, then `content_preview`, plus
`worker_id` and available name fields. NFKC/case-folded Latin words, numeric IDs
and Chinese bigrams form deterministic sparse terms, with logarithmic term
frequency and Qdrant IDF. This is sparse lexical retrieval, not a native BM25
claim. Placeholder content such as `无` is not indexed as descriptive text.
Complete worker-ID equality gets priority, without bypassing any filter. Numeric
tokens are whole tokens, not arbitrary substring matches. This aids ID/name
queries but does not guarantee every partial spelling will match.

Runtime-state and visibility payload filters are applied before keyword top-K.
The application also retains profile exclusions, metadata scope and enabled
fragment types. Both routes use the existing fragment search limit. Hits are
deduplicated by complete `profile_key`, consistent with the dense profile contract;
fragments of one profile never consume multiple rerank slots.

For `N = topK * expand_factor`, take at most N unique dense max/weighted candidates
and N unique keyword candidates. Dense eligibility uses the original aggregate
score against `vector_min_score`. Rank the union by equal-weight reciprocal-rank
fusion: `0.99 * 61/2 * (1/(60+dense_rank) + 1/(60+keyword_rank))`, omitting absent
ranks. Exact worker-ID matches receive score 1; other scores are at most 0.99.
Ties use ascending profile key. Only the first N unique profiles enter rerank.
These are ranking scores, not probabilities, cosine similarities, or model scores.

- Successful rerank: return model scores and apply `rerank_min_score`.
- Rerank disabled: return the fused order/scores, without cosine/model thresholds.
- Rerank unavailable or failed (including one failed batch or malformed/partial
  model output): with the default `reranker_fail_action=degrade`, discard all
  model scores and use the same fused fallback as disabled rerank. With
  `reranker_fail_action=empty`, return no recommendations, including when the
  adapter raises or is unavailable. Do not mix raw lexical, dense and model
  score scales.
- No keyword matches, or keyword lookup unavailable: preserve dense ordering and
  scores; failed rerank returns aggregate scores rather than invented model scores.
- Zero is a valid model score. Missing credentials, HTTP errors and incomplete
  responses must signal failure, not synthetic zero scores. The HTTP adapter
  raises errors; the fragment rerank caller applies the effective failure policy.

Existing response fields remain; response metadata adds `keyword_search_used`,
`rerank_degraded` and `score_source` (`hybrid_rrf`, `vector_weighted`, `reranker`).
`candidate_source=hybrid` identifies merged recall. INFO `keyword_search`,
`hybrid_selection`, `hybrid_decisions` and `rerank_fallback` expose counts, ranks
and selection without logging query/profile text. Contract tests live in
`tests/contract/test_keyword_retrieval.py` and run in the core acceptance gate.

## Required providers

Before creating the FastAPI application, the factory requires these registry
keys:

| Key | Contract |
| --- | --- |
| `config` | Application configuration provider |
| `secret_provider` | Secret lookup provider |
| `startup_provider` | Async application lifecycle provider |

The public provider builder also registers `context_provider` for request and
runtime context integration. Deployments may replace public implementations,
but must preserve the same provider keys and contracts.

`ProviderRegistry.require(name)` distinguishes a missing key from a registered
falsey value. Missing required providers fail during application construction;
the factory does not silently substitute another implementation.

## Worker and Profile provider contracts

Worker/Profile business behavior is owned by Avernet application services. A
deployment may replace persistence, but its providers must implement the typed,
transport-agnostic Plugin APIs in:

- `src.application.ports.worker_registry_store.WorkerRegistryStore`
- `src.application.ports.worker_profile_content_store.WorkerProfileContentStore`
- `src.domain.services.adapters.worker_runtime_state_store_adapter.WorkerRuntimeStateStoreAdapter`
- `src.domain.services.adapters.worker_profile_binding_store_adapter.WorkerProfileBindingStoreAdapter`
- `src.domain.services.adapters.worker_audit_log_adapter.WorkerAuditLogAdapter`

The registry and profile ports exchange `Worker`, `WorkerConfig`, and
`WorkerProfileContent` domain models; internal providers must not substitute
deployment-specific dictionaries. Public MySQL, SQLite, and in-memory
implementations are tested against the same contracts. Storage write and
cleanup failures propagate to the application service and must not be reported
as success.

### MySQL storage timestamp compatibility (v1)

MySQL-backed Worker/Profile providers, including injected providers, use the
database clock for record timestamps (`gmt_create`/`gmt_modify`, or
`created_at`/`updated_at` in the binding table). Inserts use schema defaults;
updates use `CURRENT_TIMESTAMP` or the schema's automatic update. Binding and
unbinding times are also generated by the database. Do not write naive UTC
application times into these session-local columns or hardcode a UTC offset.
The connection's time zone controls SQL display; deployments must configure
consistent session time zones across their pool.

Worker/Profile write results carry the persisted record timestamps, including
the original creation time on update. Reading these generated fields is part
of the write transaction: a read-back failure rolls back the write. Existing
SQLite and in-memory timestamp behavior is unchanged.

Audit `performed_at` is an event instant, not a record-write timestamp. Naive
input retains the legacy UTC interpretation; aware input preserves its offset.
MySQL receives the Unix epoch through `FROM_UNIXTIME`, and audit reads/time-range
queries retain the domain's naive-UTC convention. Database audit record times
still use the database clock. Historical rows are not rewritten automatically:
mixed old/new writers require a separately verified repair scope before any
data migration. This correction requires no schema or HTTP route changes.

During the route migration, the application factory wraps a typed-only Profile
provider with a public delivery compatibility adapter. Providers that already
offer the legacy route methods keep their original registry identity; internal
providers only need to implement the typed Plugin API. Durable Worker/Profile
records are deleted together by the registry provider. Vector data is treated
as rebuildable derived state, so vector cleanup occurs before that atomic
durable delete without deleting Profile records separately. Profile vector
cleanup covers both the legacy exact ID (`worker:profile`) and every indexed
fragment below its prefix (`worker:profile:*`).

Replaceable vector persistence providers follow the versioned
[Incremental Vector Persistence Contract v1](vector-persistence-contract-v1.md)
when they expose incremental synchronization. Baseline providers that do not
implement that optional protocol remain compatible with full index rebuilds.

The composed application exposes the canonical Worker/Profile lifecycle under
`/v1`: worker creation and lookup, online/offline transitions, profile upsert,
activation and lookup, and worker deletion. Missing workers use HTTP 404 with
`WORKER_NOT_FOUND`; duplicate creation uses HTTP 409 with
`WORKER_ALREADY_EXISTS`; a second delete is the same explicit 404 result. The
legacy `/api/v1/workers/{worker_id}/online|offline` aliases preserve those
status and error-code semantics alongside the canonical routes.

The existing direct `/v1` and `/api/v1` service paths used by Backend and
BCS/BCN are long-lived compatibility contracts. Gateway routes are additive
aliases and do not replace those direct paths. OCB's externally observable
BCSFuse behavior is the internal compatibility baseline; deployment-only
administration and diagnostics may remain conditionally mounted by the
internal composition root rather than becoming public OSS routes.

### Request identity and sync compatibility (v1)

Both composed deployments install the same request trace middleware. Trace ID
precedence is `X-Trace-ID`, then `X-Request-ID`, then a generated ID. Request
logs can read it through `trace_context`; HTTP responses carry `X-Trace-ID`.
The middleware restores the previous context on success or failure and does
not change authentication or authorization. It does not import the legacy app.

The `/v1/workers/{worker_id}/sync` and `/api/v1/workers/{worker_id}/sync`
aliases share a handler. New workers honor the supported `type` values
`human` and `bot` (default `bot`); invalid types return HTTP 422 before any
write. Updates retain an existing worker's type. With `profile.activate=false`,
sync saves the profile but preserves the existing active content, binding,
and worker mirror, including the valid state with no active profile.

With `ENABLE_PROFILE_EMBEDDING_INDEX=true`, successful explicit profile
activation also refreshes the selected profile's vectors. Other profiles'
vectors are retained, matching the existing activation policy; activation does
not imply that search returns only the selected profile. Offline workers remain
offline in vector metadata. Repeating activation is supported.

After all new fragments are successfully written, activation removes obsolete
fragment IDs belonging to that same profile (including a legacy unsplit ID).
It does not delete other profiles or workers. Enumeration and deletion errors
are reported as index refresh failures; they cannot silently acknowledge a
partial cleanup. Failed embedding or replacement writes do not start cleanup.

If indexing fails after activation is persisted,
`PUT /v1/workers/{worker_id}/profiles/{profile_id}/activate` returns HTTP 500
with detail code `ACTIVATE_PROFILE_INDEX_ERROR`, `activation_persisted=true`,
`index_updated=false`, and `retryable=true`. Binding, active content, and the
worker mirror remain committed; retrying the same activation finishes index
refresh. Index writes are not atomic with activation, so search can remain
stale until retry succeeds. When the feature is disabled, activation does not
require embedding/index providers. Persistence failures retain the existing
`ACTIVATE_PROFILE_ERROR` compensation behavior.

### Searchable Worker identifiers (v1)

Worker online/offline transitions update `runtime_state` for every vector whose
payload `worker_id` exactly matches the worker, including inactive profiles and
numbered skill fragments. Existing vectors and other payload fields are retained;
the transition does not re-embed existing profiles. Initial online publication
still builds an index when no owned vectors exist. Repeating a transition repairs
payload synchronization even when the Worker already has the requested state.
An existing-vector write failure propagates through `RUNTIME_STATE_UPDATE_ERROR`;
the saved Worker state is not rolled back, and callers can retry the same request.

Durable vector providers use `update_payload_by_worker(worker_id, updates)` when
available: it must match exact payload ownership, enumerate the durable source
(not only the local index), preserve vector values, write through to persistence
and the local index, and raise on failure. Local providers without that extension
use the existing `get_vector_ids` / `get` / `upsert` contract. Backend writes and
the Worker state update are not one transaction. Concurrent state transitions
retain the existing last-writer semantics.

Worker deletion also cleans exact-owner derived vectors that have no profile
content row (such as the generated default profile). Durable stores may expose
`delete_by_worker(worker_id)` to enumerate the durable source; implementations
must use their existing deletion/tombstone path so other instances receive the
removals. Cleanup failure prevents Worker deletion and remains retryable. No
physical deletion of internal tombstones is introduced.

Indexed `profile` and `full` fragment text includes `worker_id` before the
descriptive content. The canonical worker ID is the normalized profile's
`staff_id`, also used by the existing vector payload. Names and user-authored
profile content are not replaced; identifiers are rendered when constructing
the index, so LLM-generated text cannot erase them. IDs are not capability tags.

For colon-separated Bot IDs, the keyword tokenizer can match the complete ID,
Bot component, or user component. Exact-ID priority and threshold handling follow
the keyword recall contract above. Existing visibility, runtime-state filters
and Gateway permissions still apply; no permission bypass is introduced.

Existing persisted vectors require profile reindexing to gain this text.
Reloading Qdrant from unchanged persisted vectors alone is insufficient.
The changed fragment content also changes its hash, so the existing smart
reindex path detects it. No schema migration or automatic historical database
rewrite is performed. Public and injected internal vector stores receive the
same indexed text through the shared indexer.

## Lifecycle and failure behavior

The application lifespan awaits `startup_provider.initialize()` before serving
requests and awaits `startup_provider.shutdown()` when the application stops.
Initialization failures propagate and prevent startup. Business-route mounting
failures also propagate instead of producing a partially usable service.

Background indexing starts only after startup initialization succeeds. During
shutdown, the application waits for an active index build before shutting down
providers so that background work cannot race provider teardown.

Worker registration is a compensating operation across registry, runtime,
binding, audit, and index providers. If a post-create side effect fails, the
service removes any partially written index state and deletes the durable
worker aggregate before returning the error. Retrying the same registration
must not fail with `WORKER_ALREADY_EXISTS` because of the failed attempt.

Protected public routes use `AuthProvider.validate_request(request)` from the
published provider contract. Replacement authentication providers must
implement the complete contract rather than relying on methods available only
on the public default implementation.

The readiness endpoint reports the provider mode and readiness status. When
registry inspection fails, it returns HTTP 503 with the exception type only;
exception messages and provider values are not returned to clients.

## Ownership boundary

### Retrieval and response compatibility

The `Retriever.retrieve()` Plugin API returns
`src.domain.models.candidate_retrieval_result.CandidateRetrievalResult`: a
required `candidate_bundle` and lists of warnings, errors, and explanations.
This batch contract is distinct from the unchanged
`src.domain.models.retrieval_result.RetrievalResult` single hybrid-search hit
(`profile_key`, `score`, provenance). The baseline retriever and its application
service use the batch type; the single-hit type remains available for compatibility.
This repairs an incompatible model import without changing HTTP payloads.
The baseline retriever/service/result suites exercise this batch contract.

Baseline team matching selects online candidates using `state.runtime_state`,
not the visibility enum in `state.availability`. Its existing fallback when
all candidates are offline remains unchanged: emit `NO_AVAILABLE_WORKERS`
and consider the supplied candidate bundle. This repair does not introduce
new visibility or offline-fallback policy.

Both legacy and fragment vector matching retain the existing filtering policy:
when visibility or other vector-only fields are present, filters are delegated
to the vector provider without an additional metadata-provider intersection.
Otherwise metadata filtering and its existing logged-error fallback remain in
place. This migration does not introduce a stricter metadata failure policy.
`test_vector_metadata_filter_contract.py` covers delegation using real local
Qdrant vectors, including metadata-provider unavailability.

The broken Phase E AGENT experiment (`HybridRetrievalService`, `DenseRetriever`,
`SparseRetriever`, and `RetrievalScorer`) has been removed, not repaired or
reactivated. Repository call-site inspection found these implementations used
only by the legacy AGENT dispatch and their own tests, not the current HTTP
recommendation chain or internal composition. Existing
`ENABLE_HYBRID_RETRIEVAL=true` configurations still select the same legacy AGENT
fallback scoring, including its precedence over the explicit V2 scorer. They
no longer construct an unused embedding provider or fail through the experiment.
Filters, thresholds, ranking, and the V2 selection when the old flag is false
are covered by `test_agent_retrieval_compatibility.py`.

Historical mode enums and score/result models remain import-compatible, with
model coverage in `test_hybrid_score_compatibility.py`. Fuse business modes are
unchanged. The current fragment/keyword/RRF/optional-rerank search chain is
independent of the removed experiment and is unchanged by this cleanup.

Deprecation policy (2026-10-09): mode-based search selection, `ModeAwareScorer`,
the legacy `WorkerProfileRetrievalService.retrieve()` entrypoint, and its
explicit AGENT V2 branch are deprecated for new use. Keep existing compatibility
and fallback consumers working; do not add modes, scoring strategies, or callers.
New search consumers use `/api/v1/recommend`, whose main implementation is
`WorkerVectorMatchService`. `EXPERT_DIAGNOSIS` remains a live compatibility marker;
profile reading and `FusionMode` business behavior are not deprecated. These are
documentation-only deprecation markers, not runtime warnings or removal dates.

Fusion HTTP responses project perspectives onto the declared
`PerspectiveResponse` fields. Domain-only diagnostic `metadata` is not part
of that HTTP contract and must neither cause a 500 nor be exposed to clients.
Group identifiers retain their existing external-ID semantics without a
required application-specific prefix.

### G9 Fuse response and persistence compatibility (HTTP v1)

For `POST /api/v1/groups/{group_id}/fuse` with `bot_profile_fuse`, both
the legacy and composed routes preserve `extend_result.fused_profile`,
`extend_result.group_conversation`, and `extend_result.timing`. These are
existing G9 fields, not new top-level response fields. Other fusion modes
may return `extend_result: null`.

A generated answer is not proof that its conversation turn was persisted.
The legacy implementation logs conversation-append failures but still returns
the generated answer. This migration preserves that behavior; changing it
requires a separate business decision, not an acceptance-test assumption.
Tests verify both the returned answer and the missing history on an injected
append failure, then verify recovery on retry. An uncertain database commit
is not an exactly-once guarantee. No storage schema change is required.

`test_g9_core_acceptance.py` exercises the composed HTTP path, real merge/chat
services, and SQLite or disposable MySQL persistence. Only external model and
group-context calls are replaced. These checks verify wiring, durable writes,
profile reuse and failure reporting, not live-model quality or availability.

### Test ownership cleanup

The removed public tests have explicit replacements/owners:

- `test_mysql_provider_skeleton_contracts.py`: obsolete S29B placeholders;
  replaced by `test_mysql_worker_profile_binding_store_contract.py`,
  `test_mysql_fused_profile_store_contract.py`, and durable storage tests.
  Database-outage checks inject a failing connection pool rather than assume
  the developer's localhost MySQL must be unavailable.
- `test_main_mist_integration.py`: internal startup behavior, retained in
  OCB together with internal composition/MIST provider tests.
- `test_qdrant_zdas_vector_store.py`: internal provider behavior, retained
  in OCB. The public checkout tests its fail-closed stub using
  `test_internal_only_vector_boundary.py`; shared durable-vector behavior
  remains covered by `test_qdrant_durable_vector_store.py`.

These deletions remove duplicate tests from the wrong owner, not runtime
features. Missing OpenClaw adapter tests remain unresolved and are not removed
or skipped just to make collection succeed.

The shared Worker schema accepts non-empty identifiers, including compound
`bot_id:user_id` identifiers; it does not require a `wrk_` prefix. An active
profile key appends `:profile_id` to the complete Worker identifier and must be
split at the final colon. `state.availability` denotes visibility and accepts
`private`, `protected`, or `public`; online/offline status belongs to runtime
state, not availability. `schemas/Worker.json` follows these existing model
semantics rather than the obsolete availability/status enum.

Avernet owns the application factory, public provider contracts, public
implementations, shared business routes, and compatibility tests. Internal
repositories own their concrete providers, internal configuration, composition
root, container image, and delivery pipeline. Internal code may depend on this
public composition API; public code must not import internal packages, services,
domains, credentials, or runtime state.
