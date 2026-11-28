"""Validation invariants for the ApplicationRateLimiter SPI value types."""

from __future__ import annotations

import pytest

from gateway.community.spi.rate_limiter import (
    Decision,
    RateLimitPolicy,
    RateLimitSubject,
)


def test_subject_rejects_empty_components() -> None:
    with pytest.raises(ValueError, match="namespace"):
        RateLimitSubject(namespace="", tenant="t", app_id=1, scope="app")
    with pytest.raises(ValueError, match="tenant"):
        RateLimitSubject(namespace="n", tenant="", app_id=1, scope="app")
    with pytest.raises(ValueError, match="scope"):
        RateLimitSubject(namespace="n", tenant="t", app_id=1, scope="")


def test_subject_rejects_non_positive_app_id() -> None:
    # The invariant that there is never a shared app_id=0 bucket is enforced
    # at the type boundary too.
    with pytest.raises(ValueError, match="app_id"):
        RateLimitSubject(namespace="n", tenant="t", app_id=0, scope="app")
    with pytest.raises(ValueError, match="app_id"):
        RateLimitSubject(namespace="n", tenant="t", app_id=-5, scope="app")


def test_policy_rejects_non_positive_rate_and_sub_one_burst() -> None:
    with pytest.raises(ValueError, match="rate"):
        RateLimitPolicy(rate=0.0, burst=1)
    with pytest.raises(ValueError, match="burst"):
        RateLimitPolicy(rate=1.0, burst=0)


def test_decision_helpers() -> None:
    allow = Decision.allow(remaining_tokens=3.5)
    assert allow.allowed and allow.retry_after_seconds == 0.0
    reject = Decision.reject(retry_after_seconds=-1.0, remaining_tokens=0.0)
    # Negative retry is clamped to 0.
    assert not reject.allowed and reject.retry_after_seconds == 0.0
