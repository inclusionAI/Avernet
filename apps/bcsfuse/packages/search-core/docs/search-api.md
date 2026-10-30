# Shared search HTTP API v2

Status: opt-in initial contract. TC v1 and Fuse endpoints remain available through
the existing TC factory. Agent Search has no TC management or Fuse endpoints.
This version is additive because v1 requires Worker/Profile-specific results.

`POST /api/v2/search` and `POST /api/v2/recommend` use the same schema. The latter
currently uses the default query-based ranking; no personalization is implied.

| Request field | Contract |
| --- | --- |
| `query` | Required nonblank string, at most 4096 characters; outer whitespace is removed |
| `limit` | Integer 1–20; default 5 |
| `filters` | Field to string-array map; AND between fields, OR within each array; an empty array matches nothing |
| `scene` | Default `default`; each application must reject unsupported scenes |
| `extensions` | Business options validated by the application, never authority or provider configuration |

TC initially accepts `availability`, `runtime_state`, `domains`, `trust_level`
filters and a boolean `extensions.enable_rerank` (default true). False disables
reranking for this request; true inherits the injected TC service's configured
model and does not enable a model that the host has disabled. Agent Search's
composition root supplies its filter allowlist; no extensions are enabled yet.
Unknown fields/types are invalid. Unsupported business options return 400.

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
