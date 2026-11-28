"""Unit tests for the provider-agnostic admission business logic.

These exercise :class:`AdmissionController` and :class:`PolicyResolver` against a
fake limiter, so they assert *policy* (who is limited, how a dependency failure
is handled, which metric fires) without touching Redis. Distributed correctness
is covered by the SPI contract tests against real Redis.
"""

from __future__ import annotations

import pytest

from gateway.community.core.rate_limiting import (
    AdmissionController,
    AdmissionResult,
    PolicyResolver,
)
from gateway.community.core.rate_limiting._metrics import RateLimitMetrics
from gateway.community.spi.auth import AuthenticatedUser
from gateway.community.spi.authn import (
    AccessKey,
    AccessKeyPrincipal,
    AppPrincipal,
    Bot,
    BotPrincipal,
    PrincipalType,
    ThirdPartyApp,
    UserPrincipal,
)
from gateway.community.spi.rate_limiter import (
    Decision,
    RateLimiterUnavailableError,
    RateLimitPolicy,
    RateLimitSubject,
)


def _app_identities(*, tenant: str = "t1", app_id: int = 1) -> dict:
    app = ThirdPartyApp(app_id=app_id, app_name="demo", owners="dev", tenant=tenant)
    return {PrincipalType.APP: AppPrincipal(tenant=tenant, app=app)}


class _FakeLimiter:
    def __init__(
        self, *, decision: Decision | None = None, error: Exception | None = None
    ):
        self._decision = decision
        self._error = error
        self.calls: list[tuple[RateLimitSubject, RateLimitPolicy]] = []

    async def try_acquire(self, subject, policy):
        self.calls.append((subject, policy))
        if self._error is not None:
            raise self._error
        assert self._decision is not None
        return self._decision

    async def close(self) -> None:
        return None


class _RecordingMetrics(RateLimitMetrics):
    def __init__(self) -> None:
        self.allowed: list[RateLimitSubject] = []
        self.rejected: list[tuple[RateLimitSubject, float]] = []
        self.failures: list[tuple[RateLimitSubject, bool]] = []

    def on_allowed(self, subject):
        self.allowed.append(subject)

    def on_rejected(self, subject, *, retry_after_seconds):
        self.rejected.append((subject, retry_after_seconds))

    def on_dependency_failure(self, subject, *, ambiguous):
        self.failures.append((subject, ambiguous))


def _controller(limiter, *, metrics=None, overrides=None) -> AdmissionController:
    resolver = PolicyResolver(
        default=RateLimitPolicy(rate=5.0, burst=10),
        overrides=overrides or {},
    )
    return AdmissionController(
        limiter=limiter,
        policy_resolver=resolver,
        namespace="ns",
        metrics=metrics,
    )


# --- policy resolution ----------------------------------------------------


def test_policy_resolver_prefers_override() -> None:
    resolver = PolicyResolver(
        default=RateLimitPolicy(rate=5.0, burst=10),
        overrides={"t1:7": RateLimitPolicy(rate=1.0, burst=2)},
    )
    assert resolver.resolve(tenant="t1", app_id=7) == RateLimitPolicy(rate=1.0, burst=2)
    assert resolver.resolve(tenant="t1", app_id=8) == RateLimitPolicy(
        rate=5.0, burst=10
    )


# --- eligibility ----------------------------------------------------------


async def test_app_principal_is_limited_with_its_tenant_and_app_id() -> None:
    limiter = _FakeLimiter(decision=Decision.allow(remaining_tokens=9))
    controller = _controller(limiter)
    outcome = await controller.admit(_app_identities(tenant="acme", app_id=42))
    assert outcome.result is AdmissionResult.ALLOWED
    ((subject, policy),) = limiter.calls
    assert subject == RateLimitSubject(
        namespace="ns", tenant="acme", app_id=42, scope="app"
    )
    assert policy == RateLimitPolicy(rate=5.0, burst=10)


async def test_override_policy_is_applied() -> None:
    limiter = _FakeLimiter(decision=Decision.allow(remaining_tokens=1))
    controller = _controller(
        limiter, overrides={"acme:42": RateLimitPolicy(rate=1.0, burst=3)}
    )
    await controller.admit(_app_identities(tenant="acme", app_id=42))
    _, policy = limiter.calls[0]
    assert policy == RateLimitPolicy(rate=1.0, burst=3)


@pytest.mark.parametrize(
    "identities",
    [
        {},  # no principal at all
        {
            PrincipalType.USER: UserPrincipal(
                subject=AuthenticatedUser(id="u1", username="u1")
            )
        },
        {
            PrincipalType.BOT: BotPrincipal(
                tenant="t1",
                bot=Bot(
                    bot_uuid="b1",
                    owner_id="u1",
                    token="secret",
                    app_id=1,
                    agent_code="ac",
                    tenant="t1",
                ),
            )
        },
    ],
)
async def test_non_app_identities_are_not_applicable(identities) -> None:
    limiter = _FakeLimiter(decision=Decision.allow(remaining_tokens=1))
    controller = _controller(limiter)
    outcome = await controller.admit(identities)
    assert outcome.result is AdmissionResult.NOT_APPLICABLE
    assert limiter.calls == []  # limiter never consulted → no quota consumed


async def test_non_positive_app_id_is_not_grouped_into_shared_bucket() -> None:
    # A degenerate AppPrincipal with app_id=0 must NOT become a shared bucket.
    app = ThirdPartyApp(app_id=0, app_name="x", owners="o", tenant="t1")
    identities = {PrincipalType.APP: AppPrincipal(tenant="t1", app=app)}
    limiter = _FakeLimiter(decision=Decision.allow(remaining_tokens=1))
    controller = _controller(limiter)
    outcome = await controller.admit(identities)
    assert outcome.result is AdmissionResult.NOT_APPLICABLE
    assert limiter.calls == []


# --- outcome mapping & metrics -------------------------------------------


async def test_rejection_maps_to_rejected_with_retry_after() -> None:
    limiter = _FakeLimiter(
        decision=Decision.reject(retry_after_seconds=0.4, remaining_tokens=0.0)
    )
    metrics = _RecordingMetrics()
    controller = _controller(limiter, metrics=metrics)
    outcome = await controller.admit(_app_identities())
    assert outcome.should_reject
    assert outcome.retry_after_seconds == pytest.approx(0.4)
    assert len(metrics.rejected) == 1 and not metrics.failures and not metrics.allowed


async def test_dependency_failure_fails_open_and_is_recorded_distinctly() -> None:
    limiter = _FakeLimiter(error=RateLimiterUnavailableError("down", ambiguous=True))
    metrics = _RecordingMetrics()
    controller = _controller(limiter, metrics=metrics)
    outcome = await controller.admit(_app_identities())
    # Fail OPEN: not a rejection.
    assert outcome.result is AdmissionResult.FAILED_OPEN
    assert not outcome.should_reject
    assert metrics.failures == [(metrics.failures[0][0], True)]
    assert not metrics.rejected  # never counted as quota exhaustion


async def test_allowed_records_allowed_metric() -> None:
    limiter = _FakeLimiter(decision=Decision.allow(remaining_tokens=3))
    metrics = _RecordingMetrics()
    controller = _controller(limiter, metrics=metrics)
    outcome = await controller.admit(_app_identities())
    assert outcome.result is AdmissionResult.ALLOWED
    assert len(metrics.allowed) == 1 and not metrics.rejected and not metrics.failures
