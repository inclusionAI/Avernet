"""Conformance tests for the ``AuthStrategy`` protocol (Rule 25)."""

from __future__ import annotations

import httpx

from gateway.community.plugins.authn.bot_token import (
    BotTokenStrategy,
    InMemoryBotRegistry,
)
from gateway.community.plugins.authn.google_token import GoogleUserStrategy
from gateway.community.spi.authn import (
    AuthStrategy,
    BotPrincipal,
    CredentialBundle,
    PrincipalType,
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

    def test_declares_principal_type(self) -> None:
        assert isinstance(self.strategy.principal_type, PrincipalType)

    async def test_returns_none_when_not_applicable(self) -> None:
        result = await self.strategy.build(self.inapplicable_creds)
        assert result is None

    async def test_builds_a_principal_when_applicable(self) -> None:
        result = await self.strategy.build(self.applicable_creds)
        assert result is not None


class _GoogleTransport:
    """Build a mock httpx transport verifying the ``g`` token."""

    @staticmethod
    def build() -> httpx.MockTransport:
        def handler(request: httpx.Request) -> httpx.Response:
            auth = request.headers.get("authorization", "")
            token = auth[len("Bearer ") :] if auth.startswith("Bearer ") else ""
            if token == "g":
                return httpx.Response(200, json={"sub": "u", "email": "u@example.com"})
            return httpx.Response(401, text="invalid token")

        return httpx.MockTransport(handler)


class TestGoogleUserStrategy(AuthStrategyContract):
    def setup_method(self) -> None:
        self.strategy = GoogleUserStrategy(
            token_header="x-user-token",
            default_tenant="tenant-default",
            transport=_GoogleTransport.build(),
        )
        # A presented access token → applicable.
        self.applicable_creds = CredentialBundle(
            headers={"x-user-token": "g"}, cookies={}, query={}
        )
        # No token presented → not applicable (decline, runner fail-closes).
        self.inapplicable_creds = CredentialBundle(headers={}, cookies={}, query={})

    async def test_builds_user_principal(self) -> None:
        principal = await self.strategy.build(self.applicable_creds)
        assert isinstance(principal, UserPrincipal)


class TestBotTokenStrategy(AuthStrategyContract):
    def setup_method(self) -> None:
        self.strategy = BotTokenStrategy(
            registry=InMemoryBotRegistry(), token_header="x-bot-token"
        )
        self.applicable_creds = CredentialBundle(
            headers={"x-bot-token": "bot-key"}, cookies={}, query={}
        )
        self.inapplicable_creds = CredentialBundle(headers={}, cookies={}, query={})

    async def test_builds_bot_principal(self) -> None:
        principal = await self.strategy.build(self.applicable_creds)
        assert isinstance(principal, BotPrincipal)
