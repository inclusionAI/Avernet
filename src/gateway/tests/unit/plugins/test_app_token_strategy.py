"""Unit tests for the ``app_token`` strategy (Bearer app token + tenant → AppPrincipal)."""

from __future__ import annotations

import pytest

from gateway.community.plugins.authn.app_token import (
    AppTokenStrategy,
    StubAppTokenValidator,
    StubTenantResolver,
)
from gateway.community.spi.auth import AuthError
from gateway.community.spi.authn import AppPrincipal, CredentialBundle


def _strat() -> AppTokenStrategy:
    return AppTokenStrategy(keys=StubAppTokenValidator(), tenants=StubTenantResolver())


def _creds(headers: dict[str, str]) -> CredentialBundle:
    return CredentialBundle(headers=headers, cookies={}, query={})


async def test_absent_bearer_returns_none() -> None:
    assert await _strat().build(_creds({})) is None


async def test_unrecognized_bearer_returns_none() -> None:
    # An unrecognized Bearer is "not one of mine" → absent (US27).
    result = await _strat().build(_creds({"authorization": "Bearer nope"}))
    assert result is None


async def test_valid_token_and_tenant_builds_app_principal() -> None:
    result = await _strat().build(
        _creds({"authorization": "Bearer stub-app-token", "x-tenant-token": "t"})
    )
    assert isinstance(result, AppPrincipal)
    assert result.tenant == "stub_tenant"
    assert result.app.app_id == "stub-app"
    assert result.app.app_name == "Stub App"
    assert result.app.owners == "stub-org"
    assert result.app.app_type == "stub"
    assert result.on_behalf_of_opaque is None


async def test_on_behalf_of_opaque_passed_through() -> None:
    result = await _strat().build(
        _creds(
            {
                "authorization": "Bearer stub-app-token",
                "x-tenant-token": "t",
                "x-end-user-id": "enduser-9",
            }
        )
    )
    assert isinstance(result, AppPrincipal)
    assert result.on_behalf_of_opaque == "enduser-9"


async def test_tenant_mismatch_raises_auth_error() -> None:
    # StubTenantResolver always maps to "stub_tenant"; force a mismatch by
    # using a tenant resolver that returns a different tenant.
    from gateway.community.spi.authn import TenantResolver

    class _OtherTenant(TenantResolver):
        async def resolve(self, tenant_token: str) -> str:
            return "tenant-other"

    strat = AppTokenStrategy(keys=StubAppTokenValidator(), tenants=_OtherTenant())
    with pytest.raises(AuthError):
        await strat.build(
            _creds({"authorization": "Bearer stub-app-token", "x-tenant-token": "t"})
        )


async def test_bot_bearer_is_absent_for_app_chain_us27() -> None:
    # US27: a bot session token presented as a Bearer must NOT be treated as an
    # invalid app token by the app chain — the app strategy returns None (absent),
    # letting a bot chain resolve the same credential.
    result = await _strat().build(_creds({"authorization": "Bearer bot-key"}))
    assert result is None


async def test_jwt_shaped_bearer_is_absent_for_app_chain() -> None:
    # A JWT-shaped Bearer is neither an app token nor a bot session token here;
    # the app strategy only recognises Bearer app tokens, so it returns None
    # (the validator finds no match). US27 holds.
    result = await _strat().build(_creds({"authorization": "Bearer a.b.c"}))
    assert result is None
