# Shared search HTTP API v2

Status: opt-in initial contract. TC v1 and Fuse endpoints remain available through
the existing TC factory. Agent Search has no TC management or Fuse endpoints.
This version is additive because v1 requires Worker/Profile-specific results.

`POST /api/v2/search` and `POST /api/v2/recommend` use the same schema. The latter
currently uses the default query-based ranking; no personalization is implied.

| Request field | Contract |
| --- | --- |
| `query` | Required nonblank string, at most 4096 characters; outer whitespace is removed |
| `limit` | Strict positive integer; default 5. The default HTTP router retains the original 1–20 bound. Hosts may select `SearchBody` to let their application clamp to a runtime maximum. |
| `filters` | Field to string-array map; AND between fields, OR within each array; an empty array matches nothing |
| `scene` | Default `default`; each application must reject unsupported scenes |
| `extensions` | Business options validated by the application, never authority or provider configuration |

TC initially accepts `availability`, `runtime_state`, `domains`, `trust_level`
filters and a boolean `extensions.enable_rerank` (default true). False disables
reranking for this request; true inherits the injected TC service's configured
model and does not enable a model that the host has disabled. Agent Search's
composition root supplies its filter allowlist; no extensions are enabled yet.
Unknown fields/types are invalid. Unsupported business options return 400.

`create_search_router(request_model=SearchBody)` delegates the upper bound to
the application; the default `BoundedSearchBody` retains HTTP 422 for values
above 20, preserving TC's existing opt-in v2 contract. Agent Search selects
`SearchBody` and clamps before retrieval and cache lookup using its request's
configuration snapshot. A larger positive value is not an invalid argument in
that application. Zero, negative, boolean, string, fractional and null HTTP
values remain invalid. TR hosts reusing `SearchBody` must call the same
application normalization; the transport itself does not read DRM.

The authenticated caller and allowed IDs come from a required host callback,
never from the body. The callback accepts `fastapi.Request`, returns
`VerifiedIdentity`, and rejects invalid credentials with the host's 401/403
policy. There is no unauthenticated default. Empty allowed IDs prohibit searching;
`None` is valid only when trusted authentication explicitly grants full scope.

Successful responses contain `items`, `status` (`ok` or `degraded`) and `reasons`.
Each item has `id`, `name`, `summary`, finite `score`, `score_source` and
string-valued `extensions`. IDs are opaque. Scores are not probabilities or
comparable across sources. TC exposes complete Profile keys as IDs and returns
`worker_id` and `profile_key` in extensions; its name is empty until a reliable
name source is supplied. A valid empty result is HTTP 200 with an empty list.
Search failures must not be converted to an empty success by new providers.

Items produced by the hybrid retrieval pipeline additionally include optional
`scoreDetails`. Existing `summary`, numeric `score`, `score_source` and
`extensions` retain their meanings and types. Each present stage has a finite
`value` and a one-based ordinal `rank`:

```json
{
  "score": 0.86,
  "score_source": "reranker",
  "scoreDetails": {
    "keyword": {"value": 5.3, "rank": 2},
    "vector": {"value": 0.78, "rank": 1},
    "rrf": {"value": 0.03252247488101534, "rank": 1},
    "reranker": {"value": 0.86, "rank": 1}
  }
}
```

The numbers are illustrative. Keyword/vector values are the original index
scores, not normalized probabilities. Their ranks refer to the candidate lists
actually passed into RRF, after configured thresholds and optional source
grouping, not global ranks across the index. Grouping retains the highest-scoring
Doc per source in each branch; different branches may select different Docs.
RRF uses these ranks, not a sum of raw scores. Its rank is before reranking;
reranker rank follows the validated provider output order. Final filtering can
leave gaps in these stage ranks. Evidence never changes the existing result order.

Disabled, failed, or nonmatching stages are omitted in HTTP, not represented as
zero; zero is a valid score. An unavailable/non-finite raw score is not fabricated.
Missing evidence alone does not identify a failure: use request `status`,
`reasons` and effective policy as well. Rerank failure preserves RRF and recall
details without a reranker entry. Cache hits retain the original result's scores;
details are not proof of fresh model execution. Legacy TC adapters and direct
business-policy results without evidence omit `scoreDetails` entirely.
No additional index/model call is made to populate this object.

Hosts may additionally return `effective`, a small description of the applied
request parameters:

```json
{
  "items": [],
  "status": "degraded",
  "reasons": ["vector_disabled", "reranker_disabled"],
  "effective": {
    "limit": 20,
    "retrieval": {
      "keyword_enabled": true,
      "vector_enabled": false,
      "rerank_enabled": false
    }
  }
}
```

`effective.limit` is the normalized maximum result count, not the number of
matches. Clamping alone does not cause degradation. `effective.retrieval`
describes the switches enabled for this request; it is **not an execution
trace**. A cache hit can skip providers, and an enabled provider can fail.
`status` and `reasons` describe the result: `ok` means no reported degradation,
not proof that every provider ran; `degraded` still returns usable results
(possibly empty) with reasons such as `embedding_unavailable`,
`reranker_unavailable`, or intentionally disabled branches. HTTP errors retain
the existing error contract below, rather than masquerading as degraded success.

The business application supplies these values from the same configuration
snapshot used to execute the request, after retrieval/cache lookup. It must not
echo an unclamped request or reread configuration after a DRM update. Hosts that
cannot determine provider switches omit `retrieval`; existing TC services omit
`effective` entirely, preserving their response. No query text, authorization
scope, internal provider configuration or credentials are echoed in this object.
Non-HTTP adapters must explicitly map it into their versioned response contract;
adding it to the Python result does not update a generated RPC contract.

| Failure | HTTP status |
| --- | --- |
| Invalid schema | 422 |
| Unsupported business option | 400 |
| Execution capacity occupied (`SearchOverloaded`) | 429 |
| Declared provider failure (`SearchUnavailable`) | 503 |
| Search deadline exceeded | 504 |

Dependency errors expose generic messages, not provider details. The deadline
defaults to 2 seconds and is set by the host. It is not a capacity promise and
does not stop already-running synchronous work. TC's adapter holds its bounded
execution slot until the underlying task actually finishes. Retry policy, model
quotas and production overload/degradation behavior will be established with
real retrieval providers and load tests.

`api/services.py` is the in-process application boundary, not a second network
service. `RetrievalService` must honor the requested prefilters and trusted scope,
report unsupported options, and report failures. Providers are supplied and
closed by the host; neither importing the package nor constructing a router
initializes storage, models or update jobs.

## Optional staged retrieval and execution protection

`HybridRetrievalService.recall()` returns `RecallResult` (vector and keyword hits,
query vector, reasons and whether any recall branch completed). `rank()` fuses
and optionally reranks those candidates. `retrieve()` remains their composition.
The split permits business adapters to reuse a single query embedding. Empty
successful recall is distinct from all recall branches failing.

Strict dependency failures remain the default. A host may explicitly select
`allow_partial=True`: embedding failure keeps keyword results; one failed index
branch keeps the other; reranker failure keeps RRF ranking. Partial results carry
`status=degraded` and sanitized stage reasons. Both recall branches failing still
raises `SearchUnavailable`. Authorization violations never become partial success.

Hosts can inject stage executors through the `CallExecutor` plugin protocol.
`AsyncEmbeddingProvider.embed` and `AsyncReranker.rerank` are additive online
contracts: their implementations must use `async def`, nonblocking I/O, and
propagate cancellation. Existing synchronous model contracts remain supported.
The supplied `DeadlineExecutor` awaits native async calls directly, without
submitting them to a worker. It runs synchronous calls on one host-owned shared
`ThreadPoolExecutor`. It adds no per-stage concurrency quota, retry, rate limiter
or circuit breaker. Waiting for a runtime thread counts against the deadline;
timeout/cancellation cancels work that has not started. Python cannot stop an
already running thread. Hosts must configure finite provider network timeouts
and drain the executor before closing provider clients.

`AsyncHttpEmbedding` and `AsyncHttpReranker` share a persistent `aiohttp`
HTTP/1.1 session per provider, finite total network timeouts and no retry/sleep
loop. Cancellation closes only the affected request's socket. Idle connections
expire using the client's default keepalive timeout; no active connection or
per-host admission limit is imposed (`limit=0`, `limit_per_host=0`). Session
creation is lazy on first async use; hosts await `aclose()` after requests finish.
Error responses are consumed before returning their status, enabling reuse after
401/429 without retaining their contents in exception messages. The transport
honors proxy environment configuration and the platform TLS trust store, including
`SSL_CERT_FILE`/`SSL_CERT_DIR`. It does not follow redirects or store cookies.

Both reranker implementations share wire formatting and score validation. The
asynchronous Embedding client validates actual vector dimension and finite
values; it does not enable output dimension negotiation. A host may retain a
synchronous Embedding client for background preparation and inject the async
one solely for online queries, using the same model/index dimension. Install
the `models` extra; hosts installing with `--no-deps` must include `aiohttp` in
their runtime dependency manifest. The synchronous TC clients are unchanged.

This replaces the branch's experimental `ExecutionPool`; its `workers`,
`failure_threshold` and `cooldown_seconds` arguments are removed. The paired OCB
composition and tests migrate together. TC does not instantiate either executor;
its default behavior and the `CallExecutor.call(function, *args)` shape remain.

### Independent recall execution and request budgets

Hosts may supply separate `vector` and `keyword` executors; each falls back to
legacy `index` execution when no branch executor is supplied. Existing strict
TC behavior and the `CallExecutor.call(function, *args)` contract are unchanged.
Keyword execution starts independently of embedding. Query embedding precedes
vector lookup only on the vector branch.

An opt-in `request_budget(seconds)` context limits each configured executor to
its remaining request time. No remaining time means no new provider call;
partial retrieval can still return the successful branch or RRF order. This is
an asynchronous waiting budget, not forcible cancellation of a Python thread.
The HTTP adapter retains its outer deadline. Clients must also set finite
network timeouts; running calls may finish after their caller has timed out.

`QdrantRetrievalIndex(read_timeout_seconds=...)` bounds the Server read-lock
wait and passes a per-query Qdrant timeout rounded up to whole seconds (minimum
one second). The network query and lock wait are separate bounds. The default
zero preserves previous behavior; Local retains its existing serialization.
This does not change the longer startup/write connection timeout.

### Failure observations

`search_core.events` emits JSON message summaries with `event`, `stage`,
`reason`, `count`, `window_ms`, `timestamp`, and a sanitized `sample` containing
request ID, elapsed time, timeout and the upstream HTTP status where available.
Reasons separate timeouts, depleted request budgets, wrapped HTTP 429 quota
failures and other upstream rejections. Queries, Card bodies and exception messages are
not included. Trace IDs are internal sample IDs, not yet a BCN trace contract.

Each failure is counted; repeated keys are summarized rather than logged per
request. `record()` performs no sink I/O. Hosts flush every five seconds on a
background thread and force a final flush on orderly shutdown. A bounded queue
holds at most 128 log messages; when full, counts remain pending for a later
flush. Abrupt process loss can lose the last in-memory window; these logs are
operational diagnostics, not a durable audit trail. Hosts retain responsibility
for log rotation, retention and collection.

## 可选召回分支

`HybridRetrievalService` 组合参数新增 `keyword_enabled`、`vector_enabled`，默认均为 true，现有 TC 调用不变。向量关闭时跳过查询 Embedding 和向量调用；关键词关闭时跳过关键词调用；至少开启一路。主动关闭的分支在结果 reasons 中记为 `keyword_disabled` / `vector_disabled`，结果为 degraded。关闭分支不算成功兜底：唯一开启的分支失败仍返回检索不可用；成功但无匹配则允许空结果。参数是服务组装能力，不开放给 HTTP 调用方，也不引入 DRM 依赖。

## Optional bounded local cache primitive

`core.memory_cache.MemoryCache` is a host-owned TTL/LRU utility with entry and
estimated retained-byte limits. It copies values on reads/writes, expires entries
on access or stats collection, and exposes counters without logging content.
`clear()` invalidates entries; `retire()` also permanently disables future fills,
including late requests from a replaced configuration. There is no background
job, network dependency or request coalescing. Supported values are acyclic
retrieval result dataclasses and built-in containers/scalars; byte accounting is
an estimate, not a process RSS bound.

The primitive does not enable caching in TC or change the HTTP contract. A host
that wraps retrieval owns complete cache keys, trusted scope separation, data
revision invalidation, successful-result admission and final eligibility checks.
Hosts must keep authentication and overload protection outside cache shortcuts.

## Multiple retrieval documents per source (opt-in)

A retrieval point may carry `source_id`, `search_text`, `rerank_text` and explicit
filter fields. `search_text` feeds keyword indexing; its vector is supplied by
the host. Reranking prefers `rerank_text`, then `search_text`, then legacy
`content`. Empty rerank text falls back to recall text. Full business objects
are not required in the vector payload. The old `content`/`name` path remains
supported for TC and existing providers.

`HybridRetrievalService(group_by_source=True)` requires every returned document
to have a nonempty `source_id`. Within each recall branch it keeps the highest
scoring document per source (ties by document ID), then fuses source IDs and
reranks each source once. The vector representative supplies rerank text if a
source appears in both branches; otherwise the keyword representative is used.
There is no cross-scale comparison of raw keyword and vector scores. The host
may batch-enrich final source IDs with business display metadata.

`recall_limit` bounds documents fetched per branch, independently of the fused
source `candidate_limit`; it defaults to the latter and must be at least as
large. This is bounded over-recall, not a guarantee of filling the requested
number of distinct sources. There is no iterative refill loop.

Optional `vector_min_score` and `keyword_min_score` default to `None` (disabled).
They use higher-is-better finite raw scores and are applied after retrieval,
before grouping/fusion. They are independent of reranker scores and RRF scores;
no probability interpretation or universal numerical default is defined. A
successful branch with all candidates filtered is a valid empty result. These
options are host configuration, not untrusted HTTP request fields.

In grouped mode trusted allowed IDs refer to sources. The host index adapter
must translate this into a `source_id` prefilter, not point-ID filtering. The
service verifies returned source scope before thresholding/grouping. No implicit
list of all sources is constructed. TC keeps document/item-ID semantics because
it does not opt into grouping.

`SqlIndexDocuments.load_many(ids)` provides a bounded batch read (up to 256 IDs),
scoped by environment and index key. Source manifests, leases, atomic group
publication, retired-document deletion and final source checks belong to the
business host. No Agent Card semantics are added to the public SQL schema.

Durable documents also expose a business-neutral `source_id` (legacy fallback:
document ID). The SQL writer projects it from the point payload into an indexed
column; consumers must not maintain the two independently. `load_by_source`
reads a complete source group in one SQL statement, including tombstones, scoped
by environment and index key. The host still validates its current manifest.

`iter_version_batches` and `iter_change_versions_since` yield bounded pages of
`DocumentVersion(id, source_id, version, is_deleted, modified_at)` without reading
vectors or payloads. The latter includes the starting timestamp, sorts by
modification time and ID, and captures a timestamp upper bound. Consumers compare
versions before loading changed source groups and advance their checkpoint only
after applying the whole page. A timestamp cursor does not order transaction
commits; a lower-frequency full version check must repair late commits. These
iterators retain deletion records; physically removing them is not supported
by incremental replay. Original full-record iterators remain available.

Sync frequency, per-instance staggering, readiness and source preparation remain
host responsibilities. SQLite upgrades its local schema transactionally;
managed databases require explicit migration before this adapter is deployed.
