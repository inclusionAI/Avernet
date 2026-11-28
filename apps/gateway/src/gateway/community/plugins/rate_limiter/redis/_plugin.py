"""Redis-backed distributed token-bucket ApplicationRateLimiter.

All distributed-correctness details live here, behind the semantic SPI:

- **Atomic admission.** Refill, check, consume and persist run inside one Lua
  script (``_ADMISSION_LUA``), so a single ``EVALSHA`` is the whole admission.
  Two gateway instances sharing this Redis never read-then-write a stale token
  count — Redis executes the script atomically against the one shared bucket.

- **One authoritative clock.** The elapsed time that drives refill is measured
  from Redis's own ``TIME`` *inside* the script, not from a gateway instance's
  wall clock. Two gateways with skewed clocks therefore refill the same bucket
  consistently. A backwards clock jump is clamped to zero elapsed for the one
  request that straddles it (so the jump grants no tokens), and the stored
  timestamp follows the clock (``ts = now``) rather than a high-water mark — the
  next request measures elapsed between two post-jump readings, i.e. real time,
  and refills normally. Holding ``ts`` ahead of the clock would instead withhold
  all budget for the whole length of the jump, so it is deliberately not done.

- **Token precision.** Tokens are carried as a double and the last-update time
  as integer microseconds. A microsecond epoch (~1.7e15) is well inside a
  double's exact-integer range (2^53), so no timestamp precision is lost, and
  fractional tokens refill continuously rather than in whole-second steps.

- **Idle expiration that cannot restore budget early.** Every admission sets a
  PEXPIRE equal to the time a *completely empty* bucket needs to refill to full
  (``burst / rate``), plus a margin. Because that is always >= the time *this*
  bucket needs to reach full, the key can only be evicted once the bucket would
  already be full — so recreating it full on the next request restores nothing
  that natural refill would not have. It bounds idle memory without ever handing
  back a budget early.

- **Key isolation.** Keys are ``ratelimit:{namespace}:{tenant}:{app_id}:
  {scope}`` — deployment namespace, application identity and rule scope — with
  each variable component percent-encoded so a ``:`` inside a namespace or tenant
  cannot make two distinct subjects collide on one key. No credential or token
  ever appears in a key (or a log line); the app's surrogate id is not a secret.

- **Bounded, non-blocking, no ambiguous retry.** The async client carries a
  per-command socket timeout; a timeout or connection error becomes
  :class:`RateLimiterUnavailableError` (never a rejection), and an admission that may
  already have been applied on the server (a timeout) is flagged ``ambiguous``
  and never auto-retried.

Behavioural limits to document for operators: if Redis evicts the bucket key
under memory pressure (``maxmemory``/LRU) *before* it would naturally be full,
or if Redis restarts without persistence, the bucket is lost and the next
request starts from a full bucket — a one-time over-admission bounded by
``burst``. With ``burst`` and a short refill window this is small; size Redis so
rate-limit keys are not evicted, or give the limiter a dedicated instance.
"""

from __future__ import annotations

import math
from urllib.parse import quote, urlsplit, urlunsplit

from redis import asyncio as redis_asyncio
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import RedisError
from redis.exceptions import TimeoutError as RedisTimeoutError

from gateway.community.logger import get_logger
from gateway.community.spi.rate_limiter import (
    ApplicationRateLimiter,
    Decision,
    RateLimiterUnavailableError,
    RateLimitPolicy,
    RateLimitSubject,
)

logger = get_logger("rate_limiter")


def _redact_redis_url(url: str) -> str:
    """Return *url* reduced to ``scheme://host:port/db``, with no credentials.

    A Redis URL can carry a password two ways, and redis-py honours both: in the
    userinfo (``redis://user:pass@host/0``) and in the query string
    (``redis://host/0?password=pass``). Logging the URL verbatim would leak
    either to log readers and collectors. So this keeps only scheme, host, port
    and path (the db) — stripping userinfo *and* the whole query and fragment —
    which cannot carry a secret however the URL spells it. On any parse failure
    the URL is suppressed entirely rather than risk leaking it.
    """
    try:
        parts = urlsplit(url)
        host = parts.hostname or ""
        if parts.port is not None:
            host = f"{host}:{parts.port}"
        return urlunsplit((parts.scheme, host, parts.path, "", ""))
    except Exception:  # noqa: BLE001 — never let redaction leak the raw URL
        return "<redacted>"


# Atomic token-bucket admission. Returns integers only (Redis truncates Lua
# number returns), so sub-unit quantities are pre-scaled here and rescaled in
# Python. See the module docstring for the correctness argument.
_ADMISSION_LUA = """
local rate = tonumber(ARGV[1])
local burst = tonumber(ARGV[2])
local requested = tonumber(ARGV[3])
local ttl_ms = tonumber(ARGV[4])

local t = redis.call('TIME')
local now_us = (tonumber(t[1]) * 1000000) + tonumber(t[2])

local data = redis.call('HMGET', KEYS[1], 'tokens', 'ts')
local tokens = tonumber(data[1])
local ts = tonumber(data[2])

if tokens == nil or ts == nil then
  tokens = burst
  ts = now_us
end

-- Refill by elapsed real time, clamped so a backwards clock jump cannot grant
-- tokens. The request that straddles the jump reads elapsed < 0 and so refills
-- nothing; every later request measures elapsed between two readings of the
-- post-jump clock, which is real elapsed time, and refills correctly. The store
-- below keeps ts = now_us (not a high-water mark): holding ts ahead of the clock
-- would instead withhold legitimate budget for the entire length of the jump
-- (every call clamped to 0 while real time passes), turning a clock step into a
-- stretch of spurious 429s. Clamp-only never over-grants and self-heals at once.
local elapsed_us = now_us - ts
if elapsed_us < 0 then
  elapsed_us = 0
end
tokens = tokens + (elapsed_us / 1000000.0) * rate
if tokens > burst then
  tokens = burst
end

local allowed = 0
local retry_after_ms = 0
if tokens >= requested then
  tokens = tokens - requested
  allowed = 1
else
  local deficit = requested - tokens
  retry_after_ms = math.ceil((deficit / rate) * 1000.0)
end

redis.call('HSET', KEYS[1], 'tokens', tokens, 'ts', now_us)
redis.call('PEXPIRE', KEYS[1], ttl_ms)

return {allowed, retry_after_ms, math.floor(tokens * 1000.0)}
"""

# Lower bound on idle TTL so a tiny ``burst/rate`` window still keeps a bucket
# alive long enough to be useful, and margin added on top of the refill window.
_MIN_TTL_MS = 1000
_TTL_MARGIN_MS = 1000


class RedisTokenBucketRateLimiter(ApplicationRateLimiter):
    """Distributed token-bucket limiter over an async Redis client.

    Args:
        url: Redis connection URL (e.g. ``redis://localhost:6379/0``). A
            dedicated logical DB or instance is recommended so rate-limit keys
            are not evicted by unrelated cache pressure.
        namespace: Deployment namespace mixed into every key for isolation
            between environments sharing a backend.
        socket_timeout: Bounded per-command timeout (seconds). An expiry becomes
            a dependency failure, never a rejection.
        socket_connect_timeout: Initial connection timeout (seconds).
        key_prefix: Fixed leading key segment; kept configurable only so an
            operator can shard or re-key without code changes.

    The client is created but NOT connected here — there is deliberately no
    startup ping. Requiring storage connectivity at boot would contradict the
    fail-open availability policy (an outage would then block startup instead of
    forwarding traffic). The first request pays the connection cost, and an
    outage at that point fails open.
    """

    def __init__(
        self,
        *,
        url: str,
        namespace: str,
        socket_timeout: float = 0.25,
        socket_connect_timeout: float = 0.25,
        key_prefix: str = "ratelimit",
    ) -> None:
        if not namespace:
            raise ValueError("RedisTokenBucketRateLimiter requires a namespace")
        self._namespace = namespace
        self._key_prefix = key_prefix
        # decode_responses=False: the admission result is a list of integers,
        # and keeping bytes avoids a decode pass we do not need.
        self._redis: redis_asyncio.Redis = redis_asyncio.Redis.from_url(
            url,
            socket_timeout=socket_timeout,
            socket_connect_timeout=socket_connect_timeout,
            decode_responses=False,
        )
        self._script = self._redis.register_script(_ADMISSION_LUA)
        logger.info(
            "RedisTokenBucketRateLimiter configured url=%s namespace=%s timeout=%.3fs",
            _redact_redis_url(url),
            namespace,
            socket_timeout,
        )

    def _key(self, subject: RateLimitSubject) -> str:
        # Percent-encode each variable component before joining on ':'. A
        # namespace or tenant that itself contains ':' would otherwise let two
        # distinct subjects collapse to the same key — e.g. namespace "a:b" +
        # tenant "c" vs namespace "a" + tenant "b:c" both yield ...:a:b:c:... —
        # so deployments or tenants sharing one Redis could consume each other's
        # budgets. Encoding with safe='' maps ':' to '%3A', making the join
        # unambiguous. app_id is an int and scope is a fixed literal, but they are
        # encoded too for uniformity. No credential is present in any component.
        namespace = quote(subject.namespace, safe="")
        tenant = quote(subject.tenant, safe="")
        scope = quote(subject.scope, safe="")
        return f"{self._key_prefix}:{namespace}:{tenant}:{subject.app_id}:{scope}"

    def _ttl_ms(self, policy: RateLimitPolicy) -> int:
        refill_ms = math.ceil((policy.burst / policy.rate) * 1000.0)
        return max(_MIN_TTL_MS, refill_ms + _TTL_MARGIN_MS)

    async def try_acquire(
        self, subject: RateLimitSubject, policy: RateLimitPolicy
    ) -> Decision:
        key = self._key(subject)
        try:
            result = await self._script(
                keys=[key],
                args=[policy.rate, policy.burst, 1, self._ttl_ms(policy)],
            )
        except RedisTimeoutError as exc:
            # The command may have executed on the server before the socket read
            # timed out — the token may already be spent. Do not retry; fail open.
            raise RateLimiterUnavailableError(
                f"rate-limit backend timed out: {exc}", ambiguous=True
            ) from exc
        except RedisConnectionError as exc:
            raise RateLimiterUnavailableError(
                f"rate-limit backend unreachable: {exc}"
            ) from exc
        except RedisError as exc:
            raise RateLimiterUnavailableError(
                f"rate-limit backend error: {exc}"
            ) from exc

        try:
            allowed, retry_after_ms, remaining_milli = (
                int(result[0]),
                int(result[1]),
                int(result[2]),
            )
        except (TypeError, ValueError, IndexError) as exc:
            raise RateLimiterUnavailableError(
                f"rate-limit backend returned an unusable response: {result!r}"
            ) from exc

        remaining_tokens = remaining_milli / 1000.0
        if allowed:
            return Decision.allow(remaining_tokens=remaining_tokens)
        return Decision.reject(
            retry_after_seconds=retry_after_ms / 1000.0,
            remaining_tokens=remaining_tokens,
        )

    async def close(self) -> None:
        try:
            await self._redis.aclose()
        except Exception:  # noqa: BLE001 — shutdown must not raise
            logger.warning("error closing rate-limit Redis client", exc_info=True)
