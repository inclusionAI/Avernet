"""Conformance tests for the ``AuthStrategy`` protocol (Rule 25)."""

from __future__ import annotations

from gateway.community.plugins.auth.bare import BareAuthPlugin
from gateway.community.plugins.authn.first_party_user import FirstPartyUserStrategy
from gateway.community.spi.auth import AuthenticatedUser
from gateway.community.spi.authn import (
    AuthStrategy,
    CredentialBundle,
    StrategyParams,
    UserPrincipal,
)


class AuthStrategyContract:
    """Behaviour every ``AuthStrategy`` implementation must satisfy."""

    strategy: AuthStrategy
    applicable_creds: CredentialBundle
    inapplicable_creds: CredentialBundle

    def test_has_stable_name(self) -> None:
        assert isinstance(self.strategy.name, str)
        assert self.strategy.name

    async def test_returns_none_when_not_applicable(self) -> None:
        result = await self.strategy.build(self.inapplicable_creds, StrategyParams())
        assert result is None

    async def test_builds_a_principal_when_applicable(self) -> None:
        result = await self.strategy.build(self.applicable_creds, StrategyParams())
        assert result is not None


class TestFirstPartyUserStrategy(AuthStrategyContract):
    def setup_method(self) -> None:
        self.strategy = FirstPartyUserStrategy(
            auth=BareAuthPlugin(default_user=AuthenticatedUser(id="u", username="a")),
            default_tenant="tenant-default",
        )
        self.applicable_creds = CredentialBundle(
            headers={"cookie": "SSO_TOKEN=x"}, cookies={"SSO_TOKEN": "x"}, query={}
        )
        self.inapplicable_creds = CredentialBundle(headers={}, cookies={}, query={})

    async def test_builds_user_principal(self) -> None:
        principal = await self.strategy.build(self.applicable_creds, StrategyParams())
        assert isinstance(principal, UserPrincipal)
