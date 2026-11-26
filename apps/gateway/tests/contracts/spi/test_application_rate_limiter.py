"""SPI conformance contract for :class:`ApplicationRateLimiter`.

``ApplicationRateLimiterContract`` is the reusable behavioural contract every
provider must satisfy — the community Redis provider here, and a future
enterprise provider by subclassing it with its own ``make_limiter``. The
distributed-correctness properties (atomic admission, cross-instance sharing)
are asserted against a **real Redis**: a mock cannot establish atomicity, so the
suite skips (does not fake) when no Redis is reachable, and the run report must
say whether it actually executed.

Run with a Redis available (``REDIS_URL`` env var, default
``redis://localhost:6379/15`` — a high DB index to avoid clobbering data)::

    redis-server --daemonize yes
    pytest tests/contracts/spi/test_application_rate_limiter.py -v
"""

from __future__ import annotations

import asyncio
import math
import os
import time
import uuid

import pytest

from gateway.community.plugins.rate_limiter.redis import RedisTokenBucketRateLimiter
from gateway.community.spi.rate_limiter import (
    ApplicationRateLimiter,
    RateLimiterUnavailableError,
    RateLimitPolicy,
    RateLimitSubject,
)

_REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/15")


def _redis_reachable(url: str) -> bool:
    try:
        import redis as _sync_redis

        client = _sync_redis.Redis.from_url(
            url, socket_connect_timeout=0.5, socket_timeout=0.5
        )
        try:
            return bool(client.ping())
        finally:
            client.close()
    except Exception:
        return False


_REDIS_OK = _redis_reachable(_REDIS_URL)
_requires_redis = pytest.mark.skipif(
    not _REDIS_OK, reason=f"no Redis reachable at {_REDIS_URL}"
)


def _subject(
    namespace: str, *, app_id: int | None = None, tenant: str = "t1"
) -> RateLimitSubject:
    return RateLimitSubject(
        namespace=namespace,
        tenant=tenant,
        app_id=app_id if app_id is not None else 1,
        scope="app",
    )


class ApplicationRateLimiterContract:
    """Behavioural contract — every concrete ApplicationRateLimiter must pass it.

    A subclass provides ``make_limiter`` (a fresh limiter bound to the shared
    backend) and a unique ``namespace`` per test so buckets never collide
    between tests sharing one Redis.
    """

    def make_limiter(self) -> ApplicationRateLimiter:
        raise NotImplementedError

    async def _close(self, *limiters: ApplicationRateLimiter) -> None:
        for lim in limiters:
            await lim.close()

    def setup_method(self) -> None:
        # Unique namespace isolates this test's buckets from every other test.
        self.namespace = f"test-{uuid.uuid4().hex}"

    # --- initial capacity / exhaustion -----------------------------------

    async def test_new_bucket_starts_full(self) -> None:
        limiter = self.make_limiter()
        policy = RateLimitPolicy(rate=1.0, burst=3)
        subject = _subject(self.namespace)
        try:
            # Three immediate admissions succeed from a full bucket.
            for _ in range(3):
                assert (await limiter.try_acquire(subject, policy)).allowed
            # The fourth is a confirmed rejection with a positive retry hint.
            decision = await limiter.try_acquire(subject, policy)
            assert not decision.allowed
            assert decision.retry_after_seconds > 0
        finally:
            await self._close(limiter)

    async def test_burst_capacity_is_the_ceiling(self) -> None:
        limiter = self.make_limiter()
        policy = RateLimitPolicy(rate=0.001, burst=5)  # refill negligible in-test
        subject = _subject(self.namespace)
        try:
            allowed = 0
            for _ in range(20):
                if (await limiter.try_acquire(subject, policy)).allowed:
                    allowed += 1
            assert allowed == 5
        finally:
            await self._close(limiter)

    # --- application isolation & shared budget ----------------------------

    async def test_applications_are_isolated(self) -> None:
        limiter = self.make_limiter()
        policy = RateLimitPolicy(rate=0.001, burst=1)
        app_a = _subject(self.namespace, app_id=100)
        app_b = _subject(self.namespace, app_id=200)
        try:
            assert (await limiter.try_acquire(app_a, policy)).allowed
            # app_a is now empty, but app_b has its own full bucket.
            assert not (await limiter.try_acquire(app_a, policy)).allowed
            assert (await limiter.try_acquire(app_b, policy)).allowed
        finally:
            await self._close(limiter)

    async def test_same_application_shares_one_budget(self) -> None:
        # Two subjects equal in (namespace, tenant, app_id, scope) — e.g. two
        # different tokens of the same app — draw from the same bucket.
        limiter = self.make_limiter()
        policy = RateLimitPolicy(rate=0.001, burst=2)
        s1 = _subject(self.namespace, app_id=7)
        s2 = _subject(self.namespace, app_id=7)
        try:
            assert (await limiter.try_acquire(s1, policy)).allowed
            assert (await limiter.try_acquire(s2, policy)).allowed
            # Budget of 2 is spent regardless of which subject instance asked.
            assert not (await limiter.try_acquire(s1, policy)).allowed
        finally:
            await self._close(limiter)

    # --- refill / time boundaries -----------------------------------------

    async def test_refill_over_time(self) -> None:
        limiter = self.make_limiter()
        policy = RateLimitPolicy(rate=10.0, burst=5)
        subject = _subject(self.namespace)
        try:
            for _ in range(5):
                assert (await limiter.try_acquire(subject, policy)).allowed
            assert not (await limiter.try_acquire(subject, policy)).allowed
            # ~0.35s at 10 tok/s refills ~3.5 tokens → at least 3 admissions.
            await asyncio.sleep(0.35)
            refilled = 0
            for _ in range(5):
                if (await limiter.try_acquire(subject, policy)).allowed:
                    refilled += 1
            assert 3 <= refilled <= 4
        finally:
            await self._close(limiter)

    # --- rejection vs dependency failure ----------------------------------

    async def test_rejection_is_a_decision_not_an_exception(self) -> None:
        limiter = self.make_limiter()
        policy = RateLimitPolicy(rate=0.001, burst=1)
        subject = _subject(self.namespace)
        try:
            assert (await limiter.try_acquire(subject, policy)).allowed
            decision = await limiter.try_acquire(subject, policy)
            # A confirmed rejection is a returned Decision, never a raised error.
            assert decision.allowed is False
        finally:
            await self._close(limiter)


@_requires_redis
class TestRedisRateLimiterContract(ApplicationRateLimiterContract):
    def make_limiter(self) -> ApplicationRateLimiter:
        return RedisTokenBucketRateLimiter(
            url=_REDIS_URL,
            namespace=self.namespace,
            socket_timeout=2.0,
            socket_connect_timeout=2.0,
        )

    # --- cross-instance atomicity (real Redis only) -----------------------

    async def test_concurrent_admission_across_instances_never_oversells(self) -> None:
        """Two limiter instances, one Redis, one bucket: exactly burst allowed.

        This is the property a mock cannot prove — atomic refill/check/consume on
        the shared backend. If admission were a get-then-set under a per-process
        lock, the two instances would both read the full bucket and oversell.
        """
        a = self.make_limiter()
        b = self.make_limiter()
        policy = RateLimitPolicy(rate=0.001, burst=10)  # negligible refill
        subject = _subject(self.namespace, app_id=42)
        try:
            limiters = [a, b]
            # 40 concurrent attempts alternating between the two instances.
            results = await asyncio.gather(
                *(limiters[i % 2].try_acquire(subject, policy) for i in range(40))
            )
            allowed = sum(1 for d in results if d.allowed)
            assert allowed == 10
        finally:
            await self._close(a, b)

    async def test_idle_expiration_cannot_restore_budget_early(self) -> None:
        """The key's TTL is never shorter than the refill-to-full window.

        So the bucket can only be evicted once it would already have refilled to
        full — eviction never hands back budget the app had not yet earned.
        """
        import redis.asyncio as redis_async

        limiter = self.make_limiter()
        policy = RateLimitPolicy(rate=2.0, burst=4)
        subject = _subject(self.namespace, app_id=55)
        raw = redis_async.Redis.from_url(_REDIS_URL, decode_responses=False)
        try:
            # Exhaust the bucket.
            for _ in range(4):
                await limiter.try_acquire(subject, policy)
            key = (
                f"ratelimit:{subject.namespace}:{subject.tenant}"
                f":{subject.app_id}:{subject.scope}"
            )
            pttl_ms = await raw.pttl(key)
            # Refill-to-full for an empty bucket is burst/rate = 2.0s = 2000ms.
            refill_ms = math.ceil((policy.burst / policy.rate) * 1000.0)
            assert pttl_ms >= refill_ms
        finally:
            await raw.aclose()
            await self._close(limiter)

    async def test_backward_clock_jump_does_not_grant_extra_budget(self) -> None:
        """A backwards server-clock jump must not refill the bucket.

        Regression for the high-water-mark fix: the stored timestamp is kept at
        ``max(ts, now)``, so an empty bucket whose last update is *ahead* of the
        current clock (what a rollback leaves behind) stays empty across
        successive requests instead of refilling over already-accounted time.
        """
        import redis.asyncio as redis_async

        limiter = self.make_limiter()
        policy = RateLimitPolicy(rate=1000.0, burst=1)  # fast refill if it leaks
        subject = _subject(self.namespace, app_id=77)
        raw = redis_async.Redis.from_url(_REDIS_URL, decode_responses=False)
        key = (
            f"ratelimit:{subject.namespace}:{subject.tenant}"
            f":{subject.app_id}:{subject.scope}"
        )
        try:
            # Seed an empty bucket whose last-update time is 5s AHEAD of the
            # server clock — exactly the state a 5s backward clock jump leaves.
            secs, micros = await raw.time()
            future_us = (int(secs) * 1_000_000 + int(micros)) + 5_000_000
            await raw.hset(key, mapping={"tokens": "0", "ts": str(future_us)})

            # Even at 1000 tok/s, two requests a real moment apart must both be
            # rejected: the stored mark stays ahead, so elapsed stays <= 0.
            d1 = await limiter.try_acquire(subject, policy)
            await asyncio.sleep(0.05)
            d2 = await limiter.try_acquire(subject, policy)
            assert not d1.allowed
            assert not d2.allowed
        finally:
            await raw.aclose()
            await self._close(limiter)


class TestRedisRateLimiterDependencyFailure:
    """Dependency-failure surfacing needs no live Redis — it points at a dead port."""

    async def test_unreachable_backend_raises_unavailable_not_rejection(self) -> None:
        limiter = RedisTokenBucketRateLimiter(
            # Reserved-ish port with nothing listening; tiny timeouts.
            url="redis://127.0.0.1:6399/0",
            namespace="dep-fail",
            socket_timeout=0.2,
            socket_connect_timeout=0.2,
        )
        policy = RateLimitPolicy(rate=1.0, burst=1)
        subject = _subject("dep-fail")
        try:
            with pytest.raises(RateLimiterUnavailableError):
                await limiter.try_acquire(subject, policy)
        finally:
            await limiter.close()
