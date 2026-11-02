# BCSFuse Application Composition Contract

BCSFuse has one public source owner for shared business behavior: Avernet. A
deployment assembles environment-specific providers outside that shared code
and passes the completed registry into the public application factory.

The package layout and public/enterprise ownership are recorded in
[search architecture](search-core-boundary.md). TC uses `app`; the shared
library uses `search_core`. Enterprise-specific
business extensions remain in their host and reuse the public search contract;
they do not become part of the TC application factory.

## Public composition API

The public boundary consists of these Python APIs:

```python
ApplicationContext(
    *,
    mode: str,
    startup_profile: str,
    registry: ProviderRegistry,
)

create_bcsfuse_app(context: ApplicationContext, *, search_router=None) -> FastAPI
```

`ApplicationContext` is an explicit, already-assembled dependency container.
The factory does not select a deployment, import an internal implementation,
or read internal configuration in order to complete the provider graph.

`create_opensource_app(mode)` remains the compatibility entry point for the
public deployment. It builds the public provider registry and delegates to
`create_bcsfuse_app`.

An explicitly supplied `search_router` adds the shared v2 search/recommendation
API. The host constructs it with `TcSearchApplication`, authentication and an
owned executor. Omitting it preserves the existing v1 route surface. Existing
public and internal entrypoints do not opt in automatically. See
[shared search boundaries](search-core-boundary.md) and the
[public v2 contract](../../packages/search-core/docs/search-api.md).

## Runtime business logging

The composed app configures the shared `app` business logger at construction
and reapplies its level after provider startup. The default is `INFO` in every
environment, including prepub. Set `LOG_LEVEL=DEBUG` explicitly for diagnostics.
Accepted levels are `DEBUG`, `INFO`, `WARNING`, `ERROR`, and `CRITICAL`;
invalid values fail application construction rather than silently falling back.

Set `LOG_LEVEL` in the **running application's environment**, not just in an
image-build job. Restart the target instances to apply a change; there is no
hot-reload endpoint. At INFO/DEBUG, startup logs `business_log_level` so operators
can verify the selected level. Host handlers, formatters, filters and file
routing remain intact; their own filtering still applies. Third-party logger
levels are not raised to DEBUG. A public process without host handlers gets a
console handler with request trace IDs.

INFO retrieval logs include stage counts, thresholds, timing and removal records.
DEBUG adds candidate decision tables (IDs, scores, ranks, admission reasons) and
raw fragment hit details, in batches of at most 25 rows. These diagnostics never log query text, profile
contents, vectors or credentials.
Dependency construction, Qdrant adapter details and embedding request setup are
shown only when `LOG_LEVEL=DEBUG`; their WARNING/ERROR records remain visible at
INFO. HTTP client wire chatter stays at WARNING even in business DEBUG mode.
Initialization logs describe defaults, not the current request's rerank state.
Use `candidate_selection.rerank_enabled` for the effective request decision and
the subsequent `reranker` input/output/degradation counts for execution evidence.
Candidate selection is logged before reranking. `candidate_decisions` has an
explicit `fields` array and at most 25 compact `entries` per DEBUG record, with no
silent truncation. `max_rank` and `weighted_rank` rank eligible profiles, not
fragments. `hit_types` identifies the fragment types actually recalled.
`max_head`, `weighted_head`, `both_heads`, `max_refill`, and `weighted_refill`
indicate admission; `budget_rejected` means the profile was recalled but did not
fit the rerank budget. `fragment_type_removed`, `candidate_excluded`, and
`candidate_metadata_removed` explain earlier filtering. `reranker_returned`,
`reranker_not_returned`, `result_build_removed`, `registry_removed`,
`before_threshold`, and `threshold_removed` identify later stages. Decision
tables require DEBUG; removal ID/score batches remain at INFO. There are no
per-candidate input or metadata-save log records.
Not being returned by the reranker is not proof of a particular model score:
the model can return only its top K and existing degradation policy also applies.
`reranker_returned.score_source` distinguishes model scores from
`aggregate_fallback`; `reranker_not_returned` lists the original weighted score,
not an inferred model score.
A profile absent from both candidate decisions and removal records did not reach
the eligible pool; these logs alone cannot distinguish missing index data,
payload prefilter rejection, or a fragment outside the initial search limit.

### Bounded fragment rerank candidate selection (HTTP v1 compatible)

The eligibility filters, aggregation formula, weights and thresholds are
unchanged. To bound tail amplification, the initially recalled dense prefix is
capped by `max(64, 2 * topK * expand_factor)` before legacy fragment-type and
filter compensation. For example, the bounded default
`topK=3, expand_factor=3` fetches a 64-fragment prefix. A retained large
explicit budget such as
`topK=10, expand_factor=10` keeps the legacy 100-profile selection contract.

For rerank budget `N = topK * expand_factor`, select the top `ceil(N/2)`
eligible profiles by highest **raw** fragment similarity and top `floor(N/2)`
by existing aggregate score. Deduplicate by complete `profile_key`, then
alternate through the max and aggregate tails, skipping already selected keys,
until N unique profiles or all available profiles are selected. Ties use
ascending `profile_key`. The dense selection operates only within the bounded
fragment prefix, not the whole database.

Keyword recall uses the same profile budget `N`, not the dense fragment search
prefix. The keyword provider still counts physical fragments internally and
must refill only until N eligible unique profiles are found.

Selected candidates are supplied in aggregate-score order to preserve existing
no-provider/error fallback ordering when there are no keyword matches.
Explicitly disabling rerank without keyword matches retains aggregate-only
selection and ranking. Storage schemas and durable vectors are unchanged.

### Embedding dimension request contract

The OpenAI-compatible embedding client does not send `dimensions` by default.
Set `embedding.request_dimensions=true` (or `EMBEDDING_REQUEST_DIMENSIONS=true`)
only for model servers that explicitly support Matryoshka/custom dimensions.
When enabled, the wire request carries the composed `embedding.dimension`, and
the durable vector collection must be rebuilt at that same dimension before
search traffic is admitted. The smaller wire/vector size is a capacity change
and must be released with a quality regression, not enabled opportunistically.

The provider contract is fail-closed at both composition and response time:

- A known model cannot be composed with an unsupported dimension.
- A known non-native dimension cannot be composed unless
  `embedding.request_dimensions=true`.
- A successful JSON or binary response must return exactly the requested input
  count and the composed dimension. Mismatched counts, mismatched dimensions
  or non-finite values are provider errors; they never reach the vector store.
- A response dimension violation increments
  `bcsfuse_embedding_dimension_contract_violations_total` carrying the model,
  protocol, expected dimension, and actual dimensions.
- Semantic responses can expose `semantic_reason_code` plus the upstream
  provider reason (for example `semantic_provider_reason_code=RPM_LIMIT_EXCEEDED`)
  while keeping the stable public reason, such as
  `semantic_reason_code=embedding_quota_exhausted`,
  in `reasons` and degraded-response metadata.

### Embedding model-space index consistency

The durable index-state store also records the active embedding model and
dimension for each collection-pair. On the first deployment this signature is
registered once from the composed `embedding.model` and `embedding.dimension`.

A replica must not query or write the dense index when its configured model
space differs from the durable signature. In that state:

- dense search raises an explicit model-space mismatch error;
- hybrid retrieval continues through keyword and literal/regex fallback;
- process writes are rejected so an incompatible model cannot mix vectors;
- `/ready` reports dense unavailable, the model, dimension, and
  `embedding_index_signature_mismatch=true`;
- the response path may return a bounded degraded result, never an empty 200
  caused by the mismatch.

Changing the embedding model or dimension is a controlled index migration, not
a runtime configuration toggle. A new model-space must be fully rebuilt (ideally
in a blue-green collection) and its signature activated only after the complete
index is validated. Until then, old model vectors may serve only non-dense
fallback, not semantic search.

The model-space fence covers every dense mutation path, not only request-scope
upserts. If the current configured model or dimension differs from the durable
signature, `upsert`, payload update, deletion, `clear`,
`rebuild_from_backend`, and `sync_incremental` all fail before they reach
Qdrant or the durable vector backend. Maintenance rebuild is not a bypass for
the active model-space contract.

The signature is verified during composition/readiness and cached in the
index-state wrapper; dense requests do not add an additional SQL round trip on
the serving hot path. The durable signature is first-write-stable: subsequent
registrations for the same collection return the active signature and cannot
silently replace it. This keeps the model-space fail-safe without recreating a
per-query database bottleneck.

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
The plugin `text_search` limit still counts physical fragment hits. The lexical
candidate selector doubles the requested ranked prefix until it has enough
eligible unique profiles or the provider returns fewer hits than requested.
Grouping happens after exclusions, allowed-profile scope and fragment-type checks.
The first eligible hit determines each profile's lexical rank (highest matching
fragment score, with exact worker-ID matches first); sibling hits already fetched
are retained without duplicate appends across lookups. This does not change the
sparse scoring formula or the dense fragment recall/aggregation budgets.

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
`hybrid_selection` and `rerank_fallback` expose counts; DEBUG `hybrid_decisions`
exposes ranks and selection without logging query/profile text. Contract tests live in
`tests/contract/test_keyword_retrieval.py` and run in the core acceptance gate.

### Reranker model and document input (HTTP v1 compatible)

With `enable_rerank=true`, a request's `reranker_model` takes precedence over
the configured default and reaches the HTTP payload without mutating process
environment variables. For example:

```json
{"question": "UCT2.0 事件策略日志为空", "topK": 10, "enable_rerank": true, "reranker_model": "Qwen3-Reranker-4B"}
```

Configure `RERANKER_BASE_URL` and `RERANKER_API_KEY` for a gateway authorized
to serve the selected model. Omitting an explicit HTTP adapter model retains
`RERANKER_MODEL`, then the `bge-reranker-v2-m3` default. Qwen uses the complete
system/user prefix and assistant suffix, including `<|im_end|>` and an empty
`<think>` block. BGE retains its existing query/newline and Passage format.
Response metadata and the reranker stage log report the adapter's resolved model.

Documents contain only the Bot name and valid `contents.profile` text. Known
empty configuration boilerplate and the generated worker-ID header are removed.
Candidate IDs, retrieval scores, fragment type labels, active skills,
`capabilities`, `skill_sets`, and `full` are excluded. If no profile fragment
was recalled, selected rerank candidates are loaded from the injected Profile
content store, reading only `contents.profile` and `display_name`. A missing
profile retains an available name. Read failures retain the recalled profile/name
and are reported once with failure counts. Disabling rerank skips these reads.
Each document is capped at 8,000 characters; existing batching and failure policy
remain in effect. Name-only input is limited evidence of task capability: model
scores are not calibrated probabilities. Contract coverage is in
`tests/contract/test_reranker_profile_inputs.py`.

### Shared v2 graceful-degradation projection

When the host supplies the shared v2 search router, `TcSearchApplication`
projects TC's retrieval diagnostics into the neutral response contract. Valid
hybrid results remain `status=ok` with no reasons. A degraded result sets
`status=degraded` and reports stable reason codes in this order:
`keyword_unavailable`, `semantic_unavailable`, `literal_fallback`, and
`rerank_degraded`. A `regex_fallback` score source also reports
`literal_fallback`. Executed provider failures that bubble out of the candidate
service remain `SearchUnavailable` / HTTP 503; this projection describes only a
successful response that actually used a bounded fallback or degraded rerank. It
does not turn an empty valid result into an outage.

### V1 semantic outage and bounded fallback

Search and recommendation place the semantic-model outcome into the shared
degradation contract. If the embedding provider fails but the bounded keyword or
literal branch still produces candidates, v1 search returns HTTP 200 with
``status="degraded"`` and ``reasons=["semantic_unavailable"]``; v1 recommendation
returns the same fields in response ``metadata``. These degraded projections are
not eligible for the result cache. The stable source fields also identify the
bounded path that produced the candidates: v1 search uses
``source="keyword_fallback"`` for a semantic-outage keyword chain, and
``literal_fallback`` / ``literal_fallback_default`` for the regex chains, and
``local_snapshot`` when the process-local snapshot supplies the bounded result;
v1 recommendation reports the same source choice through ``candidate_source``.
A healthy dense-only result remains ``vector`` and a successful dense+keyword
merge remains ``hybrid``.

If an authenticated request passes the shared request boundary and is admitted to
the v1 search/recommendation application path, the delivery layer must never
turn a late dependency or core execution failure into an unexplained transport
failure. When semantic, keyword, literal, and deterministic-default fallbacks all
yield no candidates—or when retrieval/dependency composition itself fails—the
application returns a stable HTTP 200 fallback envelope:

- search sets top-level ``status="fallback_unavailable"``;
- recommendation sets ``metadata.status="fallback_unavailable"`` and
  ``metadata.candidate_source="fallback_unavailable"``;
- both expose ordered ``reasons`` ending in ``fallback_unavailable`` and a stable
  ``fallback_reason_code`` (``SEMANTIC_RETRIEVAL_UNAVAILABLE``,
  ``SEARCH_RETRIEVAL_UNAVAILABLE``, or
  ``CANDIDATE_RETRIEVAL_UNAVAILABLE``);
- provider admission state, such as embedding HTTP 429 and ``Retry-After``, is
  preserved in response metadata rather than converted into hidden success;
- no result is fabricated: the item list is empty when no legitimate candidate
  exists;
- this envelope is never stored in L1 or L2 response caches. A delivery-stage
projection failure attempts the same process-local snapshot first; if it recovers
real results, the response remains ``status="degraded"`` with
``local_snapshot_used=true`` and preserves the stage error type without counting
the request as ``fallback_unavailable``.

A healthy query with a valid, genuine no-match remains ``status="ok"`` with
zero items. A ``fallback_unavailable`` response is therefore visibly different
from a successful empty result. Prometheus records each genuinely terminal envelope in
``bcsfuse_search_fallback_unavailable_total`` with `operation` and
`reason_code` labels. Delivery-stage projection failures are included only if the
process-local snapshot also yields no result. Authentication, malformed/oversized requests, and
gateway capacity admission continue to return their existing stable errors
(401/403, 413/422, or 429) before application execution.

### V1 zero-dependency resilience

The composed `AgentSearchService` owns the last stages of availability. Composition
selects a process-local literal snapshot from the vector store
(`literal_fallback_index`), an index-version-aware resilience cache, and hard
execution budgets. Routes only bind protocol requests to those already-owned
services.

Runtime composition additionally owns a payload-only fallback snapshot file.
`vector.literal_fallback.snapshot_enabled` enables it. Runtime composition
requires an explicitly configured absolute snapshot path on a persistent volume
(`BCSFUSE_LITERAL_FALLBACK_SNAPSHOT_PATH`); common ephemeral containers such as
`/tmp`, `/var/tmp`, `/run`, and tmpfs mounts are rejected at assembly so a Pod
restart cannot silently lose the HA snapshot. Development and smoke compositions
keep this file store disabled unless a test explicitly supplies one. `vector.literal_fallback.snapshot_path` (or
`BCSFUSE_LITERAL_FALLBACK_SNAPSHOT_PATH`) selects the publish/read location.
Only the current durable-index lease owner publishes the snapshot, using an
atomic replace; read-only replicas load it but never race to overwrite the
shared file. The file records its schema version and durable `index_version`,
and contains only real durable point IDs, profile keys and payloads—never
vectors and never synthetic candidates. A corrupt, incomplete, duplicate-ID or
wrong-schema file is rejected in full rather than partially loaded.

When the durable backend is unavailable during a first boot or restart, a
previously published legitimate snapshot is loaded into the process-local
literal fallback and responses remain explicitly degraded with its source and
index version (`snapshot_source="file"` and
`snapshot_index_version`). If no valid snapshot exists, the existing explicit
`fallback_unavailable` contract remains in force. A snapshot publication error
does not fail search serving, remove readiness, or overwrite the last valid
file; `/ready` remains HTTP 200 but reports `degraded=true`,
`literal_fallback_publish_error_type`, `literal_fallback_source`,
`literal_fallback_index_version`, and `literal_fallback_profiles`.

Execution budgets are selected from the `config` provider:

- `search.execution.embedding_timeout_seconds` (default 1.50);
- `search.execution.retrieval_timeout_seconds` (default 3.00);
- `search.execution.local_snapshot_timeout_seconds` (default 0.50);
- `search.execution.total_timeout_seconds` (default 5.00).

The runtime shared MySQL pool is bounded per Pod through `MYSQL_POOL_SIZE`
(default 8, maximum 64). This prevents horizontal Pod scaling from immediately
exhausting the shared MySQL connection quota. Transient error 1040 during a
rolling scale-out is retried with bounded exponential backoff via
`MYSQL_STARTUP_RETRY_ATTEMPTS` (default 5, maximum 30) and
`MYSQL_STARTUP_RETRY_INTERVAL_MS` (default 250, maximum 5000); non-quota errors
fail fast. A failed startup retry cycle records a 30-second cooldown, so
later components fail fast instead of independently repeating the full retry
loop during rolling scale-out. After the cooldown the provider may retry again.

A stage timeout records the dependency as unavailable and continues into bounded
recovery; it never waits indefinitely for embedding, Qdrant, or recommendation
providers. The local snapshot is used both for dependency failures and for
healthy zero-hit search/zero-candidate recommendation responses before the public
empty envelope. It tries keyword matches first, then deterministic defaults. If it
returns real last-known agents, search and recommendation return
``status="degraded"`` with ``local_snapshot_used=true`` and response-specific
source information. Such a response is not a terminal failure and is not cached as
a fresh success.

The resilience cache contains two explicit layers, selected through:

- `search.resilience.enabled` (default true);
- `search.resilience.max_entries` (default 10,000);
- `search.resilience.last_known_good.ttl_seconds` (default 600);
- `search.resilience.negative.ttl_seconds` (default 10).

Last-known-good stores only a fully successful result. During dependency failure
it may be served as an explicitly stale degraded result with
``last_known_good_used=true`` and ``last_known_good_age_seconds``. Empty terminal outcomes are never persisted in the negative cache: caching an
empty fallback would turn an outage response into a short-lived guaranteed-empty
answer. Both cache keys include operation, index version, normalized query, and
request semantics; an index-version change isolates stale entries.

`ok`, `degraded`, and `fallback_unavailable` remain distinct. A valid no-match is
`ok`; stale or process-local snapshot data is `degraded`; only a dependency
failure with no legitimate candidate can be `fallback_unavailable`. None of these
layers bypasses authentication, request validation, body-size limits, or capacity
admission.

Search and recommendation response-cache middleware are optional optimizations.
Their internal L1/L2 read, key, promotion, and publish failures fail open to the
normal downstream route and increment
`bcsfuse_search_shared_response_cache_errors_total` with
`stage="preflight"`, `"promote"`, or `"publish"`. Cache instrumentation can
therefore reduce throughput, but it cannot turn an admitted valid request into an
unexplained HTTP 500.

### Search capacity admission and workers

The composed application owns a process-wide bounded search admission pool and
wait queue. It is configured through the `config` provider, not route code or
raw environment access: `search.capacity.max_inflight` (default 128),
`search.capacity.max_queue` (default 128),
`search.capacity.queue_wait_seconds` (default 2.0), and
`search.capacity.retry_after_seconds` (default 1). The pool is shared by
`/api/v1/search`, `/api/v1/recommend`, `/api/v2/search`, and
`/api/v2/recommend`. A request beyond both the execution and queue budgets, or
one that waits longer than `queue_wait_seconds`, returns HTTP 429,
`Retry-After`, and stable code `SEARCH_CAPACITY_EXCEEDED` before business
execution consumes embedding, Qdrant, rerank, or durable database capacity.
The small queue absorbs short bursts without amplifying downstream
concurrency. This is a per-process admission layer, not a distributed token
bucket.
Search transport also enforces a 64 KiB body-size boundary in the same
middleware: a POST with `Content-Length` above that value is rejected before
admission with HTTP 413 and stable code `SEARCH_REQUEST_TOO_LARGE`; when a
client omits `Content-Length`, the middleware consumes the undeclared body
under the same 64 KiB bound before JSON parsing and before an admission permit is
taken. A slow or failed body upload therefore cannot consume the process admission
slot or leak capacity; receive-side exceptions increment
`bcsfuse_search_body_read_errors_total` and remain errors rather than empty
successes. Oversized paths increment
`bcsfuse_search_request_too_large_total`. V1 search and recommendation schemas
have the same query cardinality guard as v2: query/question text is at most 4,096
characters, filters contain at most 16 fields, and each array value contains at most
32 items; violations return HTTP 422 before authentication callbacks or business
execution. These limits do not alter valid v1 responses or ranking behavior.
V1 filters now share one application-owned contract: only `availability`,
`runtime_state`, `domains`, `trust_level`, `worker_id` and `profile_key` are
accepted. Each value is a string or a string array and every value is at most
256 characters. Unsupported fields, non-string values and oversized values reject
with HTTP 422 before V2-style projection or core execution. The string-scalar
form preserves the existing V1 exact-worker filter contract.
Once a V1 request is authenticated and admitted, late dependency lookup or
execution failures at the delivery boundary use the same explicit
``fallback_unavailable`` envelope instead of an opaque HTTP 503. Logs record the
error class rather than provider details or raw query text. This is a
non-successful fallback contract, not a result-set success; transport errors are
reserved for failures that happen before the application boundary can represent
a result.

Admitted V1 search and recommendation delivery calls use a dedicated bounded
worker pool configured to the same admission value. They do not queue on
Starlette's default process worker pool once admission accepts them. Readiness
and other non-search synchronous work remains on the default pool and is thereby
protected from search saturation. `TcSearchApplication` retains its own permit
while synchronous work is actually running, including after HTTP cancellation
or timeout.

Prometheus exposes request count, rejection count, inflight, configured
capacity and request duration. The labels identify operation, API version and
HTTP outcome. These metrics describe admission health, not dependency health.

### V1 search response cache

The composed v1 search route stores a successful, fully projected search
response in the same bounded result cache used by the unified application core.
The stable v1 search projection is owned by the transport projection helper;
the route supplies authentication, timing, and cache bindings without
reconstructing result schemas inline.
The key covers the normalized query, topK, retrieval mode, minimum score,
effective default filters, reranker model, embedding-index flag, and the
durable `index_version`. Authentication runs before the middleware reads or
returns cached response data. A warmed response is returned before search
capacity admission, so a hit does not take a worker, embedding, Qdrant, rerank,
or other admission permit.

Semantic- and keyword-unavailable responses, declared literal fallback, regex
fallback, rerank-degraded responses, ``fallback_unavailable`` responses, and
responses without a valid durable index version are never stored. A miss, an invalid or oversized undeclared
body, or an index-version change replays the original request body downstream
through admission, parsing, and business execution unchanged. Miss replay is
bounded by the same request-body limit as capacity admission.

Concurrent authenticated misses with the same effective cache key are
coalesced before admission: one request executes the downstream projection,
and the other same-key requests return the stored final projection as soon as
the first successful response is cached. If the first result cannot be cached,
coalescing does not invent a success; the downstream path executes again for
the request under the same original admission semantics. Preflight search
requests are visible in the search request, duration, and inflight metrics by
HTTP outcome; cached successes and authentication failures do not consume
downstream capacity metrics.

In runtime composition BCSFuse adds the MySQL-backed shared response cache L2.
It uses the identical operation key and durable `index_version`; a shared hit is
promoted into process-local L1 and returned before admission. L2 reads and
writes are offloaded off the event loop, are bounded by the provider payload
policy, and never block a response on a failed optional write. Invalid or
unavailable L2 data is an observable metric and miss, not a fabricated empty
success. A payload is published only after the v1 route explicitly stores a
non-degraded projection in L1.

### V1 recommendation response cache

The composed v1 recommendation route stores a successful, fully projected
`BotRecommendationResponse` in the same bounded result cache used by the unified
application core. The key covers the normalized question, topK, driver/type,
effective score thresholds and rerank settings, validated filters, embedding
index flag, and the durable `index_version`. A warmed response is returned by
middleware after authentication but before search capacity admission, so a hit
does not take a worker, embedding, Qdrant, or rerank permit. The current trace
identifier is rewritten before the response is sent.

Group-context requests are not stored or answered from this response projection
because LLM question rewriting can change the effective query. Semantic- and
keyword-unavailable responses, declared literal fallback, regex fallback, and
responses without a valid durable index version are never stored. A miss replays
the original request body downstream through admission, parsing, and business
execution unchanged.

Authenticated non-group misses with the same effective cache key are coalesced
before admission: one request executes downstream and the other same-key
requests return the stored final projection with their own current trace
identifier. Group requests never use this coalescing. If the first response is
not cacheable, coalescing does not manufacture success; the downstream path
runs again under the original admission and business semantics.

The runtime MySQL L2 applies the same recommendation restriction. It never
reads or writes group-context requests. A valid non-group L2 hit is promoted
into L1, the current trace identifier is rewritten for the caller, and the
response is returned before admission. L2 read, write, and payload-validation
errors are visible as metrics and replay the original downstream semantics. The
application owns one bounded writer per search and recommendation operation and
closes both after draining accepted work during application shutdown, so restarts
do not leak optional L2 workers.

### Search dependency metrics

The public adapters expose the two dependencies that directly determine search
latency and availability without log mining:

- The durable vector adapter records `bcsfuse_qdrant_search_duration_seconds`
  and `bcsfuse_qdrant_lock_wait_seconds` for both `dense` and `keyword`
  operations. The mode label distinguishes local and server compositions.
  Failures increment `bcsfuse_qdrant_errors_total`; the adapter still raises so
  the application can execute its declared retrieval fallback or return an
  error. A metric is never used to convert a provider failure into empty
  success.
- The real HTTP reranker records `bcsfuse_rerank_calls_total` and
  `bcsfuse_rerank_duration_seconds` with `success` or `error` outcomes. Errors
  remain exceptions, leaving policy-owned fallback to the retrieval service.
- Shared L2 response-cache health uses
  `bcsfuse_search_shared_response_cache_hits_total`, `..._misses_total`,
  `..._stores_total`, and `..._errors_total` with `operation` and `stage`
  labels. The stage separates L2 `read`, `decode`, `promote`, `enqueue`, and
  `store`; a full background-write queue or a store returning false is an
  explicit error, never a success. L2 reads use one process-wide bounded reader
  shared by search and recommendation (default four concurrent backend reads);
  a slow backend or timeout consumes at most those review-visible reads and
  is treated as an optional-cache miss while the request proceeds downstream.
  The reader is process-owned and closed during application shutdown after all
  detached lookup futures finish; restarts therefore do not leak L2 reader threads.
  L2 writes use a bounded per-route writer (default 128 queued writes and 2
  workers) and do not wait in the HTTP response path. An error does not change HTTP semantics; the request proceeds
  through the declared L1 miss and downstream admission path.
- The real embedding provider records `bcsfuse_embedding_calls_total`,
  `bcsfuse_embedding_duration_seconds` and, on the final failed call,
  `bcsfuse_embedding_errors_total`. Empty batches record nothing because they do
  not contact the model service. Errors remain exceptions, so retrieval can use
  keyword-only or literal fallback through the declared pipeline.
- The no-op reranker and fake embedding provider record nothing because they are
  explicit local implementations rather than external dependency health signals.

These are process-level dependency metrics. Horizontal replicas aggregate them
in the metrics plane; they do not coordinate requests or replace index owner
leases.

### Reranker circuit breaker

Runtime and configured dev compositions wrap the HTTP reranker with a
process-local `CircuitBreakerReranker`. The default is three consecutive
failures followed by a ten-second open period; deployment overrides are
`RERANKER_CIRCUIT_FAILURE_THRESHOLD` and
`RERANKER_CIRCUIT_RECOVERY_SECONDS`. A per-process breaker is retained for each
resolved model, including request-specific reranker models.

When the circuit is open, a non-empty rerank request fails immediately with
`RerankCircuitOpenError`. It is never converted into zero scores or a silent
empty result. The existing `reranker_fail_action` owns the delivery policy:
`degrade` uses aggregate ranking and marks `rerank_degraded`, while `empty` must
be selected explicitly by policy. Empty candidate batches bypass both breaker
and backend. Half-open probes are single-flight. Prometheus exposes
`bcsfuse_rerank_circuit_open`, `bcsfuse_rerank_circuit_rejected_total`, and
transition counts by model and state.

### Durable index ownership and replica staleness

Runtime and configured dev compositions fence mutable Qdrant stores with a
shared durable index-state lease. Runtime uses the MySQL
`bcsfuse_index_states` table; dev uses `SQLiteIndexStateStore`, by default beside
the vector SQLite database and configurable through
`vector.index.sqlite.path`. One row represents one dense/keyword collection
pair and stores the active owner, lease expiry, index revision and update
time. The dev state is not optional: it supplies the durable `index_version`
required by the v1 result caches and avoids silently disabling request caching
in local and pre-production deployments. The owning application identity is supplied by
composition (`BCSFUSE_INDEX_OWNER_ID` in a pod environment), never a caller.
A second live replica is read-only and cannot reach Qdrant or the durable
backend with a write. Another replica may take over only after the prior owner
releases its lease or the lease expires.

Successful backend writes publish a new `index_version`; failed backend writes
do not. Revisions are monotonic: the state store rejects an equal or lower value,
and an owner that resumes after failover uses the shared revision as its base.
Readers expose owner, version, lag, stale, read-only and
`index_state_unavailable` state through readiness and metrics. Shared
state-store failure prevents writes and marks the replica not ready rather than
returning a false write success or empty success. Readiness reports this
explicitly as `index_state_unavailable: true`; index mutations fail closed with
HTTP 503 and stable code `INDEX_STATE_UNAVAILABLE` rather than a misleading
reader-only 403. A successful lease read clears the condition. Composition
defaults are `vector.index.stale_after_seconds=20` and
`vector.index.lease_seconds=30`; the owner heartbeat runs every ten seconds.
Owner transitions increment an audit counter, record takeover elapsed time, and
emit a business log record. Readiness exposes `index_owner_changes` and
`index_owner_takeover_seconds`; metrics expose
`bcsfuse_index_owner_changes_total` and
`bcsfuse_index_owner_takeover_seconds`.
Read replicas clear staleness only after reconstruction or sync reaches the
shared revision.

Runtime registry, profile, runtime-state, and audit stores are also lazy: they
borrow their first MySQL connection only when an operation needs it. Lazy schema
installation reuses that same operation connection rather than borrowing a second
connection from the pool. The durable vector persistence backend uses the same
injected shared pool instead of opening an independent MySQL connection, so its
connection count, retry behavior, and failure cooldown are part of the same
bounded Pod quota. A restart therefore consumes no process-wide startup
connection quota and the first recoverable operation has a bounded connection
footprint even while another replica temporarily holds the quota.

The production MySQL index-state store is constructed lazily. Its constructor
does not connect to MySQL or initialize schema; the first lease, version,
signature, or release operation does. Consequently a temporary MySQL outage
during composition does not prevent the process from completing assembly. The
index-state wrapper records `index_state_unavailable=true` and the signature
probe records the inability to verify the dense model space; dense serving and
writes then fail closed while keyword/literal fallback remains available. Once
the coordination database recovers, the next readiness lease/signature probe
clears the condition and can return the replica to normal service. The mutable
MySQL pool also resolves a bounded `MYSQL_CONNECT_TIMEOUT` (three seconds by
default), rejects non-positive timeouts, and initializes only on first use; an
unreachable database therefore fails the readiness probe quickly instead of
hanging process startup or request admission indefinitely.

`/ready` and `/metrics` wrap readiness probing in a per-process bounded
singleflight. The probe is capped at one second, repeats are served from a
0.5-second explicit result cache, and a probe that exceeds 1 second is no longer
allowed to block the event loop or flood Qdrant/MySQL. The first timeout returns
HTTP 503 with `readiness_probe_timeout=true` and `readiness_probe_inflight=true`;
a recently healthy probe may be returned for up to five seconds as an explicit
stale result with `readiness_result_stale=true` and
`readiness_cache_age_seconds`. Fresh-cache responses report
`readiness_result_cached=true` so operators can distinguish a cached status from
a live dependency check without exposing endpoint or credential details.

An explicit `QDRANT_KEYWORD_URL` may move the sparse collection to a separate
Qdrant Server while keeping the same collection-name, owner lease and shared
`index_version`. All other `QDRANT_KEYWORD_*` settings resolve independently,
but Local/Server mode must match the dense connection. Without a keyword
override, the existing single Qdrant Server and collection-pair behavior is
unchanged. Readiness uses dense Qdrant as the hard serving dependency:
`ready=true` survives a keyword-only outage only when the durable index state is
available and not stale, and explicitly reports `degraded=true` plus
`keyword_qdrant_unavailable=true`. A dense outage returns `ready=false` with
`dense_qdrant_unavailable=true`; stale index state or state-store failure also
returns 503 and cannot be masked by a successful Qdrant probe. Readiness counts
the dense collection as an additional hard dependency when the profile embedding
index is enabled. A count of zero returns 503 with `dense_index_empty=true`;
a failed count returns 503 with `index_size_unavailable=true`. A non-empty index
reports `index_size`, which makes partial backfills visible even while serving
with the declared fallback chain. Readiness reports both `qdrant_mode` and
`keyword_qdrant_mode` without exposing endpoints, provider errors or credentials.
When startup reconciliation/backfill is enabled, its status is a hard readiness
condition: `/ready` returns 503 with `index_backfill_running=true` until
reconciliation finishes, and a failed reconciliation remains 503 with
`index_backfill_failed=true` and `index_backfill_error_type` instead of being
logged as completed and admitted by the load balancer.
The `bcsfuse_index_degraded` gauge is one when dense or keyword Qdrant is
unavailable.

A lease owner may execute `rebuild_from_backend` or `sync_incremental`. A
read-only replica must not: both direct operations raise
`IndexStateWriteForbidden` before reaching Qdrant or another mutable index; HTTP
delivery maps this boundary to `INDEX_READ_ONLY`. The composed startup path calls
`synchronize_read_view` on a read-only replica instead. That path acknowledges
the shared version, reloads only the process-local literal fallback view from the
durable/shared read interface, and leaves `read_only=true`; it never issues a
Qdrant rebuild, upsert, delete or collection mutation. After startup, the
composed readiness check also uses this bounded revision sync: if a read-only
replica observes that its acknowledged revision is behind the shared revision,
it synchronizes once, reloads the local literal view, and returns ready only
when the revision and backend probes succeed. A subsequent readiness probe for
the same revision does not repeat the local reload. Revision staleness does not
imply wall-clock lag staleness; the latter still produces 503.

### Shared v2 result cache

A host that supplies the shared v2 router may enable a bounded final-result
cache on `TcSearchApplication`. Caching is not automatic: the host must supply
an `index_version_provider` from the composed vector-store state. Without that
provider, no result is cached because freshness cannot be established. Defaults
are 1,000 entries and a 30-second TTL; the host may supply explicit capacity and
a clock.

The key contains the operation, conservatively normalized query, limit, filters,
validated extensions, verified subject, the complete allowed-ID scope, and the
durable `index_version`. It never accepts identity or filters from the request
body. A cache hit returns before capacity admission, so it does not consume a
worker, embedding, Qdrant, or rerank slot. Identical concurrent requests share
one in-flight execution.

Only `status=ok` results are cached. Degraded results, provider failures, and
executor failures are never cached. An index-version change creates a new key.
Expired and LRU-removed entries are counted as evictions. This is a process-local
L1 cache, not a distributed result cache and not a substitute for dependency
health; metrics are `bcsfuse_search_result_cache_hits_total`,
`bcsfuse_search_result_cache_misses_total`,
`bcsfuse_search_result_cache_evictions_total`, and
`bcsfuse_search_result_cache_entries`.

### Query embedding cache

Runtime and configured dev compositions wrap real embedding with a
process-local query cache. Configuration comes from composition:
`embedding.cache.max_entries` (default 10000),
`embedding.cache.ttl_seconds` (default 300),
`embedding.cache.max_query_batch_size` (default 32), and adaptive query
coalescing controls: `embedding.cache.query_batch_wait_seconds` (default 0.05),
`embedding.cache.query_batch_peak_wait_seconds` (default 0.15),
`embedding.cache.query_batch_peak_threshold` (default 8), and
`embedding.cache.max_query_batch_workers` (default 1).
The cache key contains the exact model, dimension and a conservative normalized
query: Unicode NFKC plus whitespace collapsing. Case folding and alias rewriting
are not performed here. The backend receives the original text.

Concurrent identical queries share one in-flight backend call. Concurrent
unique query misses are grouped into bounded batches before crossing the
embedding circuit. The dispatcher waits for the configured short coalescing
window so bursty `search` and `recommend` traffic fills each batch instead of
immediately splitting into one-element calls. The window is zero only when
composition explicitly disables it. A bounded backend worker pool executes formed
batches in parallel while one coordinator deduplicates and fills the next batch;
the worker count is the process-local model-request fan-out. Entries use LRU
and TTL capacity, failures are not cached, and index bulk embedding bypasses
the query cache. Prometheus exposes cache hits, misses, errors, evictions,
entries, backend batch calls, batched unique queries, and backend batch sizes by
model. This is an L1 process cache only; it is not a distributed
cache or a global model quota.

### Bounded embedding retries

The real embedding provider no longer uses an unbounded five-second backoff
chain in the TC composition. Defaults are one retry, 250 ms base backoff, and a
2,000 ms retry-overhead budget. Composition supplies the same environment-based
overrides: `EMBEDDING_MAX_RETRIES`,
`EMBEDDING_RETRY_BACKOFF_SECONDS`, and `EMBEDDING_RETRY_BUDGET_MS`.
`EMBEDDING_RETRY_BACKOFF_SECONDS` remains seconds and may be fractional.

A `Retry-After` response is honored only when the next retry fits both the
configured retry count and budget. If the first call already consumed the
budget, or the provider requests a longer delay, the final error is recorded
and raised immediately. This budget bounds retry overhead, not the first HTTP
request; the first request remains bounded by `EMBEDDING_TIMEOUT_MS` and the
host transport deadline. The pipeline then applies its declared keyword-only
or literal fallback rather than turning a model failure into empty success.

### Embedding circuit breaker

Runtime and configured dev compositions place a process-local circuit breaker
between the query cache and the real embedding provider:
`cache -> circuit breaker -> real provider`. Configuration comes from
composition with `embedding.circuit.failure_threshold` (default 3) and
`embedding.circuit.recovery_seconds` (default 10).

Consecutive backend failures open the circuit. Later uncached queries fail fast
with `EmbeddingCircuitOpenError` instead of consuming the bounded search-worker
pool until each embedding timeout expires. After the recovery timeout one request
is admitted as a half-open probe; concurrent probes are rejected until it
finishes. A successful probe closes the circuit and resets the failure count;
a failed probe reopens it and restarts the timer. An empty embedding batch
bypasses both the circuit and backend. Cached query vectors remain available
while the circuit is open because the cache remains outermost.

The breaker is process-local and intentionally does not coordinate replicas or
claim distributed model health. It raises rather than returning an empty vector
or an empty result; keyword/literal fallback and error semantics remain owned by
the retrieval pipeline. Prometheus exposes:
`bcsfuse_embedding_circuit_open`, `bcsfuse_embedding_circuit_rejected_total`,
and transition counts with `from_state` and `to_state` labels.

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

- `app.application.ports.worker_registry_store.WorkerRegistryStore`
- `app.application.ports.worker_profile_content_store.WorkerProfileContentStore`
- `app.domain.services.adapters.worker_runtime_state_store_adapter.WorkerRuntimeStateStoreAdapter`
- `app.domain.services.adapters.worker_profile_binding_store_adapter.WorkerProfileBindingStoreAdapter`
- `app.domain.services.adapters.worker_audit_log_adapter.WorkerAuditLogAdapter`

The registry and profile ports exchange `Worker`, `WorkerConfig`, and
`WorkerProfileContent` domain models; internal providers must not substitute
deployment-specific dictionaries. Public MySQL, SQLite, and in-memory
implementations are tested against the same contracts. Storage write and
cleanup failures propagate to the application service and must not be reported
as success.

Worker registry providers must make `update(worker)` atomic with respect to
`worker.version`: the database write must match that version, increment it
once, and reject a conflict without writing. Sparse sync retries those
conflicts against the latest Worker so concurrent updates to different fields
do not silently overwrite each other. SQLite Worker/Profile stores also
serialize all operations on each shared connection; the SQL version predicate
coordinates writes from separate connections or processes.

Profile providers implement `save_if_version(content, expected_version)` for
sparse sync: `None` as the expected version inserts only when absent; an integer
updates only that stored version. A version mismatch returns `None` without
writing, while storage failures raise. Composed providers must support this
operation before serving sync traffic. It allows concurrent syncs on the same
Profile to re-read and re-merge without silently losing disjoint updates.

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
MySQL stores start these transactions through `start_transaction()`, which
reaches the underlying connection through both runtime pool wrappers. Assigning
`autocommit` on a wrapper does not start a database transaction. Profile/binding
switches and fused-conversation appends use the same explicit transaction rule.

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

Single-vector consumers may use `PointLookupVectorPersistenceBackend.load_by_id`
to read an exact active ID. Providers without this optional extension retain
the existing full-read fallback. `WorkerScopedProfileSource.scan_for_workers`
preserves `scan()` lifecycle/runtime filtering, active-record conversion, source
priority, and legacy `bot_` alias merging while limiting results to exact owners.
Built-in stores implement `WorkerScopedProfileContentStore.get_active_by_workers`
with a Worker ID predicate at the SQL boundary. Worker index builds retain
active-profile preference and the existing first-profile fallback; these lookup
extensions do not change HTTP responses or embedding/update policy.

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

For existing Workers and Profiles, sync is presence-aware. `name` is required
and replaced; omitted mutable Worker fields (`description`, `responsibilities`,
`domains`, `capabilities`, `skills`, `availability`, `trust_level`) retain their
stored values. Supplied values replace them, including empty lists or an empty
description. Omitted `runtime_state` or explicit null preserves the current
runtime state; new Workers default online. Existing Profile scalar fields,
`skill_sets`, and generated analysis fields survive omission. Supplied
`skill_sets` replaces the list, including `[]`. Supplied nonempty `contents`
and `metadata` maps merge by key; an explicitly empty map clears that map.
Supplied `profile.summary` replaces `contents.ecb_summary`; null removes it.
Concurrent syncs use versioned Worker and Profile writes and retry stale merges
against the latest committed record up to five times. Exhausted Worker
conflicts return 500 `SYNC_WORKER_ERROR`; exhausted Profile conflicts return
500 `PROFILE_SYNC_FAILED`. Neither is acknowledged as a successful write.
This does not make Worker and Profile writes one transaction.
LLM analysis may replace only generated `contents` keys after successful
analysis. Before writing, it reads the current Profile and skips a stale
result when that Profile no longer matches the analyzed snapshot. This check
does not provide cross-process compare-and-swap for a write racing after the
read. A missing Profile provider returns 503, and
Profile read/write/activation failures return 500 `PROFILE_SYNC_FAILED` rather
than a false success. Worker changes made before such a failure may remain
committed; repeating the same sync safely completes the Profile step.
SQLite and MySQL Profile stores persist `description` under the reserved
`__bcsfuse_profile_description_v1__` key in the existing `metadata` JSON
column. Reads remove that storage key from caller-visible metadata. The key
is written even when the description is null, so clearing a description does
not revive a value from a previously added column; rows without the key can
still read such a legacy column when present. The reserved key cannot be used
as caller metadata. This contract requires no table alteration, including in
internal DBA-managed deployments.

With `ENABLE_PROFILE_EMBEDDING_INDEX=true`, successful explicit profile
activation also refreshes the selected profile's vectors. Other profiles'
vectors are retained, matching the existing activation policy; activation does
not imply that search returns only the selected profile. Offline workers remain
offline in vector metadata. Repeating activation is supported.

Explicit activation builds semantic content only from the selected
`worker_id/profile_id` in the application's profile content store. Registry
descriptions and skills do not override it, including when the selected content
is shorter or its fields or skills have been removed.

After all new fragments are successfully written, activation removes obsolete
fragment IDs belonging to that same profile (including a legacy unsplit ID).
It does not delete other profiles or workers. Enumeration and deletion errors
are propagated. Prefix matches require explicit matching `profile_key` and
`worker_id` metadata because compound IDs can share prefixes. Only the exact
legacy unsplit ID may omit ownership metadata. Ambiguous legacy prefix matches
are retained rather than guessed to belong to the target. Cleanup failures
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
The composed `/online` and `/offline` routes wait for their Worker state writes
and vector synchronization before returning the existing response. Synchronous
service work runs in a thread pool so it does not block the server event loop.
Before publishing online vector payloads, the service re-reads the Worker's
current runtime and lifecycle state and skips work for a missing, offline,
inactive, or disabled Worker. This preserves the earlier deferred path's check;
it does not make the state check and vector write atomic across processes.
Vector write failures retain the existing route error behavior. Slow callers
should dispatch their lifecycle HTTP requests in the background rather than
relying on BCSFuse to acknowledge incomplete work. Repeating a transition retries
payload synchronization; initial online publication still builds missing vectors.

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

Full index construction groups fragment embedding text across profiles in
provider batches. The default batch size is 32, matching the currently wrapped
embedding server's request maximum; deployment providers with a different
maximum must explicitly configure the indexer batch size. The background indexer
also paces batch calls by 0.25 seconds. A failed embedding batch does not upsert
any fragment from that batch and marks every profile in the failed batch as
failed so the next startup or activation can retry the whole profile. Because
`ProfileEmbeddingStore::upsert_embeddings` writes through the composed vector
store, successful batch writes must not trigger an additional incremental-sync
operation that is unsupported by some durable adapters.

Background indexing starts only after startup initialization succeeds. A
non-empty existing index is not treated as an index-complete signal. Startup
scans current profiles, computes their exact fragment IDs, and backfills only
profiles with missing fragments. If an existing index cannot be inspected, the
startup path fails closed and schedules a full rebuild rather than preserving an
unknown partial index. During shutdown, the application waits for an active index
build before shutting down providers so that background work cannot race provider
teardown.

Worker registration is a compensating operation across registry, runtime,
binding, audit, and index providers. If a post-create side effect fails, the
service removes any partially written index state and deletes the durable
worker aggregate before returning the error. Retrying the same registration
must not fail with `WORKER_ALREADY_EXISTS` because of the failed attempt.

Protected public routes use `AuthProvider.validate_request(request)` from the
published provider contract. Replacement authentication providers must
implement the complete contract rather than relying on methods available only
on the public default implementation.

Gateway authentication is an explicit composition decision read through the
config provider at `auth.trust_gateway`, with a fail-closed default of
`false`. `BCSFUSE_TRUST_GATEWAY` may override the YAML value while configuration
is loaded, but route code must not read the environment directly. Enabling this
value globally skips the local Bearer validation on every route guarded by
`require_oss_auth`; such a deployment must expose BCSFuse only through the
authenticated gateway and never through a direct debug, singlebox, or sidecar
port.

The readiness endpoint reports the provider mode and readiness status. When
registry inspection fails, it returns HTTP 503 with the exception type only;
exception messages and provider values are not returned to clients.

## Ownership boundary

### Retrieval and response compatibility

The `Retriever.retrieve()` Plugin API returns
`app.domain.models.candidate_retrieval_result.CandidateRetrievalResult`: a
required `candidate_bundle` and lists of warnings, errors, and explanations.
This batch contract is distinct from the unchanged
`app.domain.models.retrieval_result.RetrievalResult` single hybrid-search hit
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


### Shared retrieval and local storage

TC adapter import paths remain available, reexporting vector values and wrapping
shared model/storage execution. Composition loads TC environment settings and
retains its diagnostics; shared providers receive explicit configuration. Generic
sparse retrieval has no Worker-ID priority; the TC keyword adapter enables it.
`dev` and `dev_smoke` now compose Qdrant Local with SQLite vector persistence and
startup rebuilding. Existing FAISS files are left untouched and are not automatic
migration inputs. Re-sync test Workers into a new local dataset; SQLite snapshots
replace FAISS snapshot artifacts. No online TC database migration is required.

### Shared embedding quota degradation

Embedding HTTP 429 is mapped to the explicit reason
`embedding_quota_exhausted` and a positive semantic retry window. When a
provider supplies `Retry-After`, that value wins; otherwise
`embedding.quota.recovery_seconds` (default 60 seconds) supplies the fallback
block.

Runtime replicas persist that condition in
`bcsfuse_embedding_capacity_states` through MySQL. Configured dev deployments
use a SQLite store beside the vector database for co-located replicas. Once any
replica observes 429, replicas share the block and fail fast before another
model call; callers fall back to the bounded keyword/literal chain. The
capacity-store operation intentionally fails open to the existing process-local
circuit if durable coordination itself is unavailable, so a coordination outage
does not become a total search outage.

In addition to the post-429 block, runtime uses
`bcsfuse_embedding_call_windows` as a proactive shared admission counter.
`embedding.quota.max_calls_per_minute` (default 30) counts upstream provider
calls rather than unique queries; `embedding.quota.period_seconds` defaults to
60. When the shared window is exhausted, replicas fail fast with the same
embedding-quota reason and continue serving the bounded keyword/literal chain.
A disabled or unavailable window fails open to the local circuit so coordination
outages cannot become total search outages.

Both tables contain only model-level counters and expiry metadata. They never
store a provider URL, token, query, or response body.
