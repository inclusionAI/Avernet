"""Unit tests for the first_party_user auth strategy."""

from __future__ import annotations

import pytest

from gateway.community.plugins.auth.bare import BareAuthPlugin
from gateway.community.plugins.authn.first_party_user import FirstPartyUserStrategy
from gateway.community.spi.auth import AuthenticatedUser, AuthError
from gateway.community.spi.authn import (
    CredentialBundle,
    Delegation,
    StrategyParams,
    UserPrincipal,
)

_DEFAULT_TENANT = "tenant-default"


def _creds_with_session() -> CredentialBundle:
    return CredentialBundle(
        headers={"cookie": "SSO_TOKEN=abc"}, cookies={"SSO_TOKEN": "abc"}, query={}
    )


def _strategy(user: AuthenticatedUser) -> FirstPartyUserStrategy:
    return FirstPartyUserStrategy(
        auth=BareAuthPlugin(default_user=user), default_tenant=_DEFAULT_TENANT
    )


async def test_no_session_cookie_is_not_applicable() -> None:
    strategy = _strategy(AuthenticatedUser(id="u1", username="alice"))
    result = await strategy.build(
        CredentialBundle(headers={}, cookies={}, query={}), StrategyParams()
    )
    assert result is None


async def test_builds_user_principal_with_identity_tenant() -> None:
    strategy = _strategy(
        AuthenticatedUser(id="u1", username="alice", tenant_id="tenant-9")
    )
    principal = await strategy.build(_creds_with_session(), StrategyParams())
    assert isinstance(principal, UserPrincipal)
    assert principal.tenant == "tenant-9"
    assert principal.subject.id == "u1"
    assert principal.scopes == frozenset()


async def test_falls_back_to_default_tenant() -> None:
    strategy = _strategy(AuthenticatedUser(id="u1", username="alice"))  # no tenant_id
    principal = await strategy.build(_creds_with_session(), StrategyParams())
    assert isinstance(principal, UserPrincipal)
    assert principal.tenant == _DEFAULT_TENANT


async def test_forbidden_delegation_with_session_raises() -> None:
    strategy = _strategy(AuthenticatedUser(id="u1", username="alice"))
    with pytest.raises(AuthError):
        await strategy.build(
            _creds_with_session(), StrategyParams(delegation=Delegation.FORBIDDEN)
        )


class _RaisingAuth:
    async def get_login_user(
        self, cookie: str | None = None, referer: str | None = None
    ) -> AuthenticatedUser:
        raise AuthError("invalid session")

    def is_allowed(self, user: AuthenticatedUser) -> bool:
        return True

    def check_permission(
        self,
        user_id: str,
        permission_codes: str,
        request_url: str = "",
        request_map: str = "",
    ) -> bool:
        return True


async def test_invalid_session_is_hard_failure() -> None:
    strategy = FirstPartyUserStrategy(
        auth=_RaisingAuth(), default_tenant=_DEFAULT_TENANT
    )
    with pytest.raises(AuthError):
        await strategy.build(_creds_with_session(), StrategyParams())
