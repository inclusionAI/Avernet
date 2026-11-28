# Per-application distributed rate limiting (Open API Gateway)

Scope: `apps/gateway` (the Python Open API Gateway), **not** the BCS distributed
Gateway. Avernet owns the semantic PluginAPI, the Gateway integration,
configuration, the community Redis provider, and reusable contract tests. OCB
later adds an enterprise storage provider (TBase/ZCache) and registers it in its
composition root. No public code depends on OCB or internal infrastructure.

## 1. What it does

A token-bucket admission check limits how fast one **application** (authenticated
`AppPrincipal`, keyed by `tenant` + `app_id`) may drive HTTP forwarding requests,
with the budget shared across all Gateway instances and across all of that
application's tokens. It is **disabled by default**.

Outcomes are kept strictly distinct:

| Outcome | HTTP behaviour |
| --- | --- |
| Allowed | forward (one token consumed) |
| Confirmed rejection (budget exhausted) | **429**, upstream not called, `Retry-After` (seconds, rounded up, ≥1) |
| Storage failure / timeout | **fail open** — forward, record separately from rejections |

A storage outage is never reported as quota exhaustion (never 429/503 for an
unavailable backend). An ambiguous timeout is not retried (the token may already
be spent) — it fails open.

## 2. File locations

Semantic PluginAPI (SPI) — the only seam business logic depends on:
- `src/gateway/community/spi/rate_limiter/_models.py` — `RateLimitSubject`,
  `RateLimitPolicy`, `Decision`.
- `src/gateway/community/spi/rate_limiter/_protocols.py` —
  `ApplicationRateLimiter.try_acquire(subject, policy) -> Decision`.
- `src/gateway/community/spi/rate_limiter/_errors.py` — `RateLimiterError`,
  `RateLimiterUnavailableError` (dependency-failure signal, carries `ambiguous`).

Core business logic (provider- and HTTP-agnostic):
- `src/gateway/community/core/rate_limiting/_controller.py` —
  `AdmissionController` (eligibility, subject building, fail-open), `AdmissionOutcome`.
- `src/gateway/community/core/rate_limiting/_policy.py` — `PolicyResolver`
  (default + per-app overrides).
- `src/gateway/community/core/rate_limiting/_metrics.py` — `RateLimitMetrics`
  seam (dependency failures counted separately from rejections).

Community Redis provider:
- `src/gateway/community/plugins/rate_limiter/redis/_plugin.py` —
  `RedisTokenBucketRateLimiter` (async client + atomic Lua admission).

HTTP integration & composition:
- `src/gateway/community/adapters/web/_forward.py` — admission step, after route
  resolution + successful auth, before any upstream byte or response header.
- `src/gateway/community/bootstrap/plugins/_plugin_core.py` — `rate_limiter`
  provider `Selector` (the enterprise-registration seam).
- `src/gateway/community/bootstrap/_rate_limiting.py` — builds the controller;
  fails startup when enabled with no provider.
- `src/gateway/community/config/_models.py` — `RateLimitConfig`,
  `RateLimitPolicyConfig`, `plugins.rate_limiter`.

Tests:
- `tests/contracts/spi/test_application_rate_limiter.py` — **reusable** provider
  contract (`ApplicationRateLimiterContract`), run against real Redis.
- `tests/unit/core/rate_limiting/test_admission.py` — eligibility / fail-open / mapping.
- `tests/integration/test_forward_rate_limit.py` — HTTP 429 / fail-open / streaming.
- `tests/unit/bootstrap/test_rate_limiting_composition.py` — fail-startup vs disabled.
- `tests/unit/plugins/rate_limiter/test_redis_rate_limiter.py` — error mapping (mocked).
- `tests/unit/spi/test_rate_limiter_models.py` — value-type invariants.

## 3. Configuration

Under `user_config` (see `configs/application.yaml` for the annotated block):

```yaml
user_config:
  plugins:
    rate_limiter: "redis"        # "noop" (default) wires no limiter
  rate_limit:
    enabled: true
    namespace: "prod-sg"         # key isolation between deployments
    default:
      rate: 50.0                 # sustained QPS
      burst: 100                 # bucket capacity in requests (>= 1)
    overrides:
      "tenant-a:12345":          # "<tenant>:<app_id>"
        rate: 10.0
        burst: 20
  rate_limit_redis:
    url: "redis://localhost:6379/0"
    socket_timeout: 0.25         # bounded; limiter fails open
    socket_connect_timeout: 0.25
    key_prefix: "ratelimit"
```

Rules enforced:
- Disabled by default (backward compatible).
- `rate > 0`, `burst >= 1` — refused at config load.
- `enabled: true` requires `rate_limit.default` (config-load error) **and** a
  real `plugins.rate_limiter` provider (startup `ConfigError`). No silent
  fallback to unlimited or process-local limiting.
- `rate`/`burst` are QPS/capacity, deliberately **not** reinterpreted RPM/RPD
  fields (`extra: forbid` rejects stray keys).
- Startup does **not** require Redis connectivity (that would contradict
  fail-open); the first request pays connection cost and an outage fails open.

## 4. OCB: registering an enterprise provider

The `rate_limiter` selector in `PluginContainer` is the registration seam — the
same mechanism as every other plugin. In OCB's composition root, at import time:

```python
from dependency_injector import providers
from gateway.community.plugin_registry import register_plugin_option_provider

def _tbase_rate_limiter(container):
    cfg = container.config
    return providers.Singleton(
        TbaseTokenBucketRateLimiter,                 # implements ApplicationRateLimiter
        namespace=cfg.rate_limit.namespace,
        # ...enterprise connection config, read from its own config section...
    )

register_plugin_option_provider("rate_limiter", "tbase", _tbase_rate_limiter)
```

Then a deployment sets `plugins.rate_limiter: "tbase"`. No community change is
needed; the community package never imports OCB. The provider must implement the
`ApplicationRateLimiter` Protocol and satisfy the contract in §5 — verify it by
subclassing `ApplicationRateLimiterContract` with a `make_limiter` bound to the
enterprise backend.

## 5. Provider contract (what an enterprise provider must satisfy)

**Atomicity.** `try_acquire` must refill, check, consume and persist bucket state
as **one atomic operation on the shared backend**. It must not be implemented as
a separate read + write under a process-local lock — that does not serialise two
Gateway instances and oversells the budget. (Community proves this with a single
Lua `EVALSHA`.)

**Timing / clock.** Refill must be driven by a single authoritative clock so
Gateway instances with skewed wall clocks stay consistent; a backwards clock jump
must not grant tokens. (Community reads Redis server `TIME` inside the script;
tokens are doubles, timestamps integer microseconds — exact within 2^53.)

**Expiration.** Idle buckets may expire to bound memory, but expiry must never
restore budget early: the TTL must be ≥ the time the bucket needs to refill to
full (`burst / rate`), so a key can only vanish once it would already be full.

**Failure semantics.** A backend outage, bounded-timeout expiry, or unusable
response must raise `RateLimiterUnavailableError` (never a rejected `Decision`).
An ambiguous timeout must set `ambiguous=True` and must **not** be auto-retried.
Timeouts must be bounded and I/O non-blocking on the event loop.

**Isolation & hygiene.** Keys must be isolated by deployment namespace,
application identity, and rule scope. No credential may appear in a key or log.
The public surface must not expose backend keys, clients, scripts, commands,
SDK types, connection credentials, or HTTP objects.

## 6. Validation results

Environment: Python 3.13, `redis-server` 7.x local, `redis-py` 7.4.0.

- **Contract tests against real Redis** — 9 passed, incl.
  `test_concurrent_admission_across_instances_never_oversells` (two limiter
  instances, one Redis, 40 concurrent attempts → exactly `burst` admitted; a
  mock cannot prove this) and `test_idle_expiration_cannot_restore_budget_early`
  (PTTL ≥ refill-to-full window). Dependency-failure test points at a dead port
  and asserts `RateLimiterUnavailableError`, not a rejection.
- **Gateway HTTP integration** — 7 passed: 429 + `Retry-After: 1` with upstream
  never called; storage failure forwards (200); allowed forwards once; auth
  failure consumes no quota; no-AppPrincipal preserves behaviour; streaming
  (`text/event-stream`) stays streaming; admission `None` leaves the path
  untouched.
- **Core / composition / models / mocked-provider** — 100% line coverage on all
  new `rate_limiter` modules.
- **Full suite** — 1289 passed, 5 skipped (excluding two pre-existing,
  environment-only failures: `tests/e2e/.../test_health.py` needs a running
  server, and `test_dump_and_publish_script` needs a clean subprocess venv —
  both unrelated to this change). Ruff lint + format clean; architecture
  (layering) tests pass.

Run the real-Redis contract suite with:

```bash
redis-server --daemonize yes
REDIS_URL=redis://localhost:6379/15 pytest tests/contracts/spi/test_application_rate_limiter.py -v
```

Without a reachable Redis those tests **skip** (they never fake atomicity).

## 7. Known limitations

- **Lost bucket state.** If Redis evicts a rate-limit key under `maxmemory`/LRU
  before it would naturally refill to full, or restarts without persistence, the
  bucket is lost and the next request starts from a full bucket — a one-time
  over-admission bounded by `burst`. Mitigation: a dedicated Redis DB/instance
  for rate-limit keys, sized so they are not evicted.
- **Scope of MVP.** HTTP forwarding requests with an authenticated `AppPrincipal`
  only. No WebSocket message-level limiting. No per-route/per-method rules (the
  `scope` key segment reserves room for them). No management UI, dynamic config
  service, or new DB tables.
- **Metrics.** The `RateLimitMetrics` seam is a no-op by default; it logs
  allowed/rejected/dependency-failure distinctly. Wiring a real counter sink is a
  deployment/enterprise concern.
- **Enterprise provider Lua.** TBase/ZCache Lua support is not assumed; atomicity
  there is the enterprise provider's responsibility, verified by the shared
  contract test.
