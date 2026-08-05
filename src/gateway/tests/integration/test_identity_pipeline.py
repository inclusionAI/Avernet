"""Integration smoke for the real identity pipeline (runner + identity chains).

Builds the real :func:`build_authenticator` with an explicitly initialised
SQLite plugin, so the bot/access-key/app paths exercise the DB-backed
registries end-to-end. The google path swaps the USER chain to a
``GoogleUserStrategy`` with an ``httpx.MockTransport`` so no real network call.
"""

from __future__ import annotations

import httpx
import pytest

from gateway.community.bootstrap import initialize_database
from gateway.community.bootstrap._authn import build_authenticator
from gateway.community.bootstrap._configs import DatabaseConfig
from gateway.community.config import UserConfig
from gateway.community.core.access_key import AccessKeyRepository
from gateway.community.core.app import AppRepository
from gateway.community.core.authn import IdentityChain, authenticate
from gateway.community.core.bot import BotRepository
from gateway.community.plugins.authn.access_key_token import AccessKeyTokenStrategy
from gateway.community.plugins.authn.app_token import AppTokenStrategy
from gateway.community.plugins.authn.bot_token import BotTokenStrategy
from gateway.community.plugins.authn.google_token import GoogleUserStrategy
from gateway.community.plugins.database.sqlite import SqliteDatabasePlugin
from gateway.community.spi.auth import AuthError
from gateway.community.spi.authn import (
    CredentialBundle,
    Presence,
    PrincipalType,
)
from gateway.community.spi.database import DataSourcePlugin


def _userinfo_handler(
    body: dict[str, object], status: int = 200
) -> httpx.MockTransport:
    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=body)

    return httpx.MockTransport(_handler)


_GOOGLE_BODY = {"sub": "g-1", "email": "a@example.com", "name": "A"}


def _db() -> DataSourcePlugin:
    return initialize_database(
        SqliteDatabasePlugin(), DatabaseConfig(plugin_type="SQLITE_ORM", db_url="")
    )


def _strategy_pool(db: DataSourcePlugin):
    return {
        "google": GoogleUserStrategy(
            token_header="x-avernet-google-token", default_tenant="default"
        ),
        "bot_token": BotTokenStrategy(registry=BotRepository(db)),
        "app_token": AppTokenStrategy(registry=AppRepository(db)),
        "access_key_token": AccessKeyTokenStrategy(registry=AccessKeyRepository(db)),
    }


def _strategies() -> dict[PrincipalType, IdentityChain]:
    db = _db()
    return build_authenticator(
        strategies=_strategy_pool(db), user_config=UserConfig()
    ).strategies


def _google_strategies() -> dict[PrincipalType, IdentityChain]:
    """Strategies with a mock-google user strategy (no real network)."""
    db = _db()
    strategies = build_authenticator(
        strategies=_strategy_pool(db), user_config=UserConfig()
    ).strategies
    strategies[PrincipalType.USER] = IdentityChain(
        PrincipalType.USER,
        (
            GoogleUserStrategy(
                token_header="x-avernet-google-token",
                default_tenant="default",
                transport=_userinfo_handler(_GOOGLE_BODY),
            ),
        ),
    )
    return strategies


async def test_app_only_resolves_app_identity() -> None:
    creds = CredentialBundle(
        headers={"x-avernet-app-token": "app-key"},
        cookies={},
        query={},
    )
    result = await authenticate(
        creds, {PrincipalType.APP: Presence.REQUIRED}, _strategies()
    )
    assert PrincipalType.APP in result
    assert PrincipalType.USER not in result


async def test_bot_only_resolves_bot_identity() -> None:
    creds = CredentialBundle(
        headers={"x-avernet-bot-token": "bot-key"}, cookies={}, query={}
    )
    result = await authenticate(
        creds, {PrincipalType.BOT: Presence.REQUIRED}, _strategies()
    )
    assert PrincipalType.BOT in result


async def test_google_user_resolves_user_identity() -> None:
    creds = CredentialBundle(
        headers={"x-avernet-google-token": "tok"}, cookies={}, query={}
    )
    result = await authenticate(
        creds, {PrincipalType.USER: Presence.REQUIRED}, _google_strategies()
    )
    assert PrincipalType.USER in result


async def test_bot_token_absent_for_app_required_denies() -> None:
    # A bot token on an app-required route: app chain returns None (absent) →
    # bot is optional here, app required-missing → 401.
    creds = CredentialBundle(
        headers={"x-avernet-bot-token": "bot-key"}, cookies={}, query={}
    )
    with pytest.raises(AuthError):
        await authenticate(
            creds,
            {
                PrincipalType.APP: Presence.REQUIRED,
                PrincipalType.BOT: Presence.OPTIONAL,
            },
            _strategies(),
        )


async def test_bot_token_satisfies_bot_required_with_app_optional() -> None:
    # US27: a bot token on a (app optional, bot required) route resolves bot
    # and leaves app absent — no terminal raise from the app chain.
    creds = CredentialBundle(
        headers={"x-avernet-bot-token": "bot-key"}, cookies={}, query={}
    )
    result = await authenticate(
        creds,
        {PrincipalType.APP: Presence.OPTIONAL, PrincipalType.BOT: Presence.REQUIRED},
        _strategies(),
    )
    assert PrincipalType.BOT in result
    assert PrincipalType.APP not in result


async def test_mixed_app_and_google_user_resolve_both() -> None:
    creds = CredentialBundle(
        headers={
            "x-avernet-google-token": "tok",
            "x-avernet-app-token": "app-key",
        },
        cookies={},
        query={},
    )
    result = await authenticate(
        creds,
        {PrincipalType.USER: Presence.REQUIRED, PrincipalType.APP: Presence.OPTIONAL},
        _google_strategies(),
    )
    assert PrincipalType.USER in result
    assert PrincipalType.APP in result
