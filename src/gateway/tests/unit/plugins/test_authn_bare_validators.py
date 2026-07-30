"""Tests for the bare (open-source) authn validator stubs."""

from __future__ import annotations

import pytest

from gateway.community.plugins.authn.app_token import (
    StubAppTokenValidator,
    StubTenantResolver,
)
from gateway.community.spi.auth import AuthError


async def test_app_token_validator_match_returns_record() -> None:
    v = StubAppTokenValidator()
    rec = await v.verify("stub-app-token")
    assert rec is not None
    assert rec.tenant == "stub_tenant"


async def test_app_token_validator_no_match_returns_none() -> None:
    v = StubAppTokenValidator()
    assert await v.verify("non-existent-token") is None


async def test_bare_tenant_resolver_requires_token() -> None:
    r = StubTenantResolver()
    with pytest.raises(AuthError):
        await r.resolve("")


async def test_bare_tenant_resolver_maps_to_fixed_tenant() -> None:
    r = StubTenantResolver()
    assert await r.resolve("any-non-empty") == "stub_tenant"
