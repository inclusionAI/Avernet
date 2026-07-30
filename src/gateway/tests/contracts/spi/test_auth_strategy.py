"""Conformance tests for the AuthStrategy contract (Rule 25)."""

from __future__ import annotations

from datetime import datetime

import httpx

from gateway.community.plugins.authn.access_key_token import AccessKeyTokenStrategy
from gateway.community.plugins.authn.app_token import (
    AppTokenStrategy,
    StubAppTokenValidator,
    StubTenantResolver,
)
from gateway.community.plugins.authn.bot_token import BotTokenStrategy
from gateway.community.plugins.authn.google_token import GoogleUserStrategy
from gateway.community.spi.access_key import RegisteredAccessKey
from gateway.community.spi.authn import CredentialBundle, PrincipalType
from gateway.community.spi.bot import RegisteredBot


def _userinfo_handler(
    body: dict[str, object], status: int = 200
) -> httpx.MockTransport:
    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=body)

    return httpx.MockTransport(_handler)


_GOOGLE_BODY = {"sub": "g-1", "email": "a@example.com", "name": "A"}


class _FakeBotRegistry:
    """Resolves only ``bot-key``; else None (soft miss). No DB."""

    _BOT = RegisteredBot(bot_uuid="bot-7", owner_id="owner-1", tenant="t")

    async def find_bot_by_token(self, token: str) -> RegisteredBot | None:
        return self._BOT if token == "bot-key" else None


class _FakeAccessKeyRegistry:
    """Resolves only ``ak-token``; else None (soft miss). No DB."""

    _AK = RegisteredAccessKey(
        access_key_id="ak-1", tenant="t", expire_at=datetime(2027, 1, 1, 0, 0, 0)
    )

    async def find_access_key_by_token(self, token: str) -> RegisteredAccessKey | None:
        return self._AK if token == "ak-token" else None


class AuthStrategyContract:
    """Behaviour every AuthStrategy implementation must satisfy."""

    strategy: object  # AuthStrategy
    applicable_creds: CredentialBundle
    inapplicable_creds: CredentialBundle

    def test_has_stable_name(self) -> None:
        assert isinstance(getattr(self.strategy, "name", None), str)
        assert self.strategy.name  # type: ignore[attr-defined]

    def test_has_principal_type(self) -> None:
        assert isinstance(getattr(self.strategy, "principal_type", None), PrincipalType)

    async def test_returns_none_when_not_applicable(self) -> None:
        result = await self.strategy.build(self.inapplicable_creds)  # type: ignore[attr-defined]
        assert result is None

    async def test_builds_a_principal_when_applicable(self) -> None:
        result = await self.strategy.build(self.applicable_creds)  # type: ignore[attr-defined]
        assert result is not None


class TestGoogleUserStrategy(AuthStrategyContract):
    def setup_method(self) -> None:
        self.strategy = GoogleUserStrategy(
            token_header="x-google-token",
            default_tenant="t-default",
            transport=_userinfo_handler(_GOOGLE_BODY),
        )
        self.applicable_creds = CredentialBundle(
            headers={"x-google-token": "tok"}, cookies={}, query={}
        )
        self.inapplicable_creds = CredentialBundle(headers={}, cookies={}, query={})

    async def test_builds_user_principal(self) -> None:
        from gateway.community.spi.authn import UserPrincipal

        result = await self.strategy.build(self.applicable_creds)  # type: ignore[attr-defined]
        assert isinstance(result, UserPrincipal)


class TestBotTokenStrategy(AuthStrategyContract):
    def setup_method(self) -> None:
        self.strategy = BotTokenStrategy(registry=_FakeBotRegistry())
        self.applicable_creds = CredentialBundle(
            headers={"x-bot-token": "bot-key"}, cookies={}, query={}
        )
        self.inapplicable_creds = CredentialBundle(headers={}, cookies={}, query={})

    async def test_builds_bot_principal(self) -> None:
        from gateway.community.spi.authn import BotPrincipal

        result = await self.strategy.build(self.applicable_creds)  # type: ignore[attr-defined]
        assert isinstance(result, BotPrincipal)


class TestAppTokenStrategy(AuthStrategyContract):
    def setup_method(self) -> None:
        self.strategy = AppTokenStrategy(
            keys=StubAppTokenValidator(), tenants=StubTenantResolver()
        )
        self.applicable_creds = CredentialBundle(
            headers={"authorization": "Bearer stub-app-token", "x-tenant-token": "t"},
            cookies={},
            query={},
        )
        self.inapplicable_creds = CredentialBundle(headers={}, cookies={}, query={})

    async def test_builds_app_principal(self) -> None:
        from gateway.community.spi.authn import AppPrincipal

        result = await self.strategy.build(self.applicable_creds)  # type: ignore[attr-defined]
        assert isinstance(result, AppPrincipal)


class TestAccessKeyTokenStrategy(AuthStrategyContract):
    def setup_method(self) -> None:
        self.strategy = AccessKeyTokenStrategy(registry=_FakeAccessKeyRegistry())
        self.applicable_creds = CredentialBundle(
            headers={"x-access-key-token": "ak-token"}, cookies={}, query={}
        )
        self.inapplicable_creds = CredentialBundle(headers={}, cookies={}, query={})

    async def test_builds_access_key_principal(self) -> None:
        from gateway.community.spi.authn import AccessKeyPrincipal

        result = await self.strategy.build(self.applicable_creds)  # type: ignore[attr-defined]
        assert isinstance(result, AccessKeyPrincipal)
