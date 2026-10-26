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
