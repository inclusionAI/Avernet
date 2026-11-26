"""Unit tests for RedisTokenBucketRateLimiter error-mapping and key shape.

These patch the Redis client so they run without a live server (CI without
Redis still exercises the branches). Real-Redis atomicity and cross-instance
behaviour are covered by ``tests/contracts/spi/test_application_rate_limiter.py``.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import RedisError
from redis.exceptions import TimeoutError as RedisTimeoutError

from gateway.community.plugins.rate_limiter.redis import RedisTokenBucketRateLimiter
from gateway.community.plugins.rate_limiter.redis._plugin import _redact_redis_url
from gateway.community.spi.rate_limiter import (
    RateLimiterUnavailableError,
    RateLimitPolicy,
    RateLimitSubject,
)


def test_redact_redis_url_strips_credentials() -> None:
    # Credentials in the URL must never survive into a log line.
    assert (
        _redact_redis_url("redis://user:s3cret@host:6379/0")
        == "redis://***@host:6379/0"
    )
    assert _redact_redis_url("rediss://:tokenonly@host/2") == "rediss://***@host/2"
    # No credentials -> URL kept as-is (host/port/db are useful to operators).
    assert _redact_redis_url("redis://localhost:6379/0") == "redis://localhost:6379/0"
    # A password must not leak through any component of the result.
    assert "s3cret" not in _redact_redis_url("redis://user:s3cret@host:6379/0")
    # A URL that fails to parse (here, an out-of-range port) is suppressed
    # entirely rather than risk leaking credentials it may carry.
    suppressed = _redact_redis_url("redis://user:s3cret@host:99999/0")
    assert suppressed == "<redacted>"
    assert "s3cret" not in suppressed


class _FakeRedis:
    def __init__(self) -> None:
        self.closed = False

    def register_script(self, _lua: str):
        return object()  # replaced per-test via plugin._script

    async def aclose(self) -> None:
        self.closed = True


def _make(script) -> RedisTokenBucketRateLimiter:
    with patch(
        "gateway.community.plugins.rate_limiter.redis._plugin."
        "redis_asyncio.Redis.from_url",
        return_value=_FakeRedis(),
    ):
        plugin = RedisTokenBucketRateLimiter(url="redis://x:6379/0", namespace="ns")
    plugin._script = script  # type: ignore[assignment]  # noqa: SLF001
    return plugin


_SUBJECT = RateLimitSubject(namespace="ns", tenant="t1", app_id=5, scope="app")
_POLICY = RateLimitPolicy(rate=10.0, burst=20)


def test_requires_namespace() -> None:
    with patch(
        "gateway.community.plugins.rate_limiter.redis._plugin."
        "redis_asyncio.Redis.from_url",
        return_value=_FakeRedis(),
    ):
        with pytest.raises(ValueError, match="namespace"):
            RedisTokenBucketRateLimiter(url="redis://x:6379/0", namespace="")


async def test_key_format_and_args() -> None:
    captured = {}

    async def script(*, keys, args):
        captured["keys"] = keys
        captured["args"] = args
        return [1, 0, 19000]

    plugin = _make(script)
    # key = ratelimit:{namespace}:{tenant}:{app_id}:{scope}
    await plugin.try_acquire(_SUBJECT, _POLICY)
    assert captured["keys"] == ["ratelimit:ns:t1:5:app"]
    rate, burst, requested, ttl_ms = captured["args"]
    assert (rate, burst, requested) == (10.0, 20, 1)
    # ttl >= refill-to-full (burst/rate = 2s = 2000ms) plus margin.
    assert ttl_ms >= 2000


async def test_allowed_decision_mapping() -> None:
    async def script(*, keys, args):
        return [1, 0, 12500]  # allowed, retry 0ms, 12.5 tokens left

    decision = await _make(script).try_acquire(_SUBJECT, _POLICY)
    assert decision.allowed
    assert decision.remaining_tokens == pytest.approx(12.5)
    assert decision.retry_after_seconds == 0.0


async def test_rejected_decision_mapping() -> None:
    async def script(*, keys, args):
        return [0, 150, 0]  # rejected, retry 150ms, 0 tokens

    decision = await _make(script).try_acquire(_SUBJECT, _POLICY)
    assert not decision.allowed
    assert decision.retry_after_seconds == pytest.approx(0.15)


async def test_timeout_is_ambiguous_unavailable() -> None:
    async def script(*, keys, args):
        raise RedisTimeoutError("read timed out")

    with pytest.raises(RateLimiterUnavailableError) as exc:
        await _make(script).try_acquire(_SUBJECT, _POLICY)
    assert exc.value.ambiguous is True


async def test_connection_error_is_non_ambiguous_unavailable() -> None:
    async def script(*, keys, args):
        raise RedisConnectionError("refused")

    with pytest.raises(RateLimiterUnavailableError) as exc:
        await _make(script).try_acquire(_SUBJECT, _POLICY)
    assert exc.value.ambiguous is False


async def test_unusable_response_is_unavailable() -> None:
    async def script(*, keys, args):
        return None  # malformed

    with pytest.raises(RateLimiterUnavailableError):
        await _make(script).try_acquire(_SUBJECT, _POLICY)


async def test_generic_redis_error_is_non_ambiguous_unavailable() -> None:
    async def script(*, keys, args):
        raise RedisError("NOSCRIPT or similar")

    with pytest.raises(RateLimiterUnavailableError) as exc:
        await _make(script).try_acquire(_SUBJECT, _POLICY)
    assert exc.value.ambiguous is False


async def test_close_is_idempotent_and_swallows_errors() -> None:
    async def script(*, keys, args):
        return [1, 0, 0]

    plugin = _make(script)

    async def _boom() -> None:
        raise RuntimeError("client already closed")

    plugin._redis.aclose = _boom  # type: ignore[attr-defined]  # noqa: SLF001
    # close() must never raise during shutdown, even if the client errors.
    await plugin.close()
