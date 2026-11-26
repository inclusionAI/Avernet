"""Composition-root wiring + config validation for rate limiting.

Covers the fail-startup vs fail-open boundary: configuration errors (enabled
with no provider, invalid budget) are refused at load/boot; runtime storage
unavailability is NOT checked here (it fails open at request time).
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from gateway.community.bootstrap._configs import ConfigError
from gateway.community.bootstrap._rate_limiting import build_admission
from gateway.community.config import RateLimitConfig, RateLimitPolicyConfig, UserConfig
from gateway.community.core.rate_limiting import AdmissionController
from gateway.community.spi.rate_limiter import Decision, RateLimitPolicy


class _FakeLimiter:
    async def try_acquire(self, subject, policy):
        return Decision.allow(remaining_tokens=1)

    async def close(self) -> None:
        return None


def _user_config(rate_limit: RateLimitConfig) -> UserConfig:
    return UserConfig(rate_limit=rate_limit)


# --- build_admission ------------------------------------------------------


def test_disabled_builds_no_controller() -> None:
    uc = _user_config(RateLimitConfig(enabled=False))
    assert build_admission(user_config=uc, limiter=_FakeLimiter()) is None


def test_enabled_without_provider_fails_startup() -> None:
    uc = _user_config(
        RateLimitConfig(enabled=True, default=RateLimitPolicyConfig(rate=5, burst=10))
    )
    # limiter=None models plugins.rate_limiter='noop' (no provider wired).
    with pytest.raises(ConfigError, match="no.*provider|noop"):
        build_admission(user_config=uc, limiter=None)


def test_enabled_with_provider_builds_controller_with_policies() -> None:
    uc = _user_config(
        RateLimitConfig(
            enabled=True,
            namespace="prod",
            default=RateLimitPolicyConfig(rate=5, burst=10),
            overrides={"acme:42": RateLimitPolicyConfig(rate=1, burst=2)},
        )
    )
    controller = build_admission(user_config=uc, limiter=_FakeLimiter())
    assert isinstance(controller, AdmissionController)
    resolver = controller._policies  # noqa: SLF001 - asserting wiring
    assert resolver.default == RateLimitPolicy(rate=5.0, burst=10)
    assert resolver.resolve(tenant="acme", app_id=42) == RateLimitPolicy(
        rate=1.0, burst=2
    )
    assert controller._namespace == "prod"  # noqa: SLF001


# --- config validation (fail at load) ------------------------------------


def test_enabled_requires_default_budget() -> None:
    with pytest.raises(ValidationError, match="default"):
        RateLimitConfig(enabled=True)  # no default


def test_non_positive_rate_is_rejected() -> None:
    with pytest.raises(ValidationError):
        RateLimitPolicyConfig(rate=0, burst=10)


def test_sub_one_burst_is_rejected() -> None:
    with pytest.raises(ValidationError):
        RateLimitPolicyConfig(rate=5, burst=0)


def test_unknown_rate_limit_field_is_rejected() -> None:
    # extra='forbid' guards against RPM/RPD-style fields being silently accepted
    # and misread as QPS/burst.
    with pytest.raises(ValidationError):
        RateLimitConfig(
            enabled=True, default=RateLimitPolicyConfig(rate=5, burst=10), rpm=600
        )
