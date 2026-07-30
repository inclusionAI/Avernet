"""Unit tests for the ``bot_token`` strategy (bot session token → BotPrincipal).

Uses a tiny in-test fake registry (no DB) so the strategy's extraction/adjudication
logic is exercised in isolation; the DB-backed registry has its own test.
"""

from __future__ import annotations

from gateway.community.plugins.authn.bot_token import (
    BotTokenStrategy,
    is_jwt_format,
)
from gateway.community.spi.authn import BotPrincipal, CredentialBundle
from gateway.community.spi.bot import RegisteredBot


class _FakeBotRegistry:
    """Resolves only ``bot-key`` → a fixed RegisteredBot; else None (soft miss)."""

    _BOT = RegisteredBot(
        bot_uuid="bot-7", owner_id="owner-1", app_id="app-1", tenant="t"
    )

    async def find_bot_by_token(self, token: str) -> RegisteredBot | None:
        return self._BOT if token == "bot-key" else None


def _strat() -> BotTokenStrategy:
    return BotTokenStrategy(registry=_FakeBotRegistry())


def _creds(headers: dict[str, str]) -> CredentialBundle:
    return CredentialBundle(headers=headers, cookies={}, query={})


async def test_dedicated_header_wins_over_authorization() -> None:
    creds = _creds({"x-avernet-bot-token": "bot-key", "authorization": "Bearer other"})
    result = await _strat().build(creds)
    assert isinstance(result, BotPrincipal)
    assert result.bot.token == "bot-key"


async def test_bearer_non_jwt_resolves_via_registry() -> None:
    result = await _strat().build(_creds({"authorization": "Bearer bot-key"}))
    assert isinstance(result, BotPrincipal)
    assert result.tenant == "t"
    assert result.bot.bot_uuid == "bot-7"
    assert result.bot.owner_id == "owner-1"
    assert result.bot.app_id == "app-1"
    assert result.bot.tenant == "t"
    assert result.bot.token == "bot-key"


async def test_bearer_jwt_shaped_returns_none() -> None:
    # A JWT-shaped Bearer is left for a (future) JWT strategy — US27.
    result = await _strat().build(_creds({"authorization": "Bearer a.b.c"}))
    assert result is None


async def test_dedicated_header_with_jwt_is_taken_as_is() -> None:
    # The dedicated header wins and bypasses the JWT-shape check.
    result = await _strat().build(_creds({"x-avernet-bot-token": "a.b.c"}))
    # The registry does not know this token → soft miss → None.
    assert result is None


async def test_unknown_token_returns_none_soft_miss() -> None:
    assert await _strat().build(_creds({"x-avernet-bot-token": "nope"})) is None


async def test_absent_token_returns_none() -> None:
    assert await _strat().build(_creds({})) is None


async def test_bare_token_resolves() -> None:
    # A bare (non-Bearer) token in the authorization header is also accepted.
    result = await _strat().build(_creds({"authorization": "bot-key"}))
    assert isinstance(result, BotPrincipal)
    assert result.bot.token == "bot-key"


def test_is_jwt_format_heuristic() -> None:
    assert is_jwt_format("a.b.c") is True
    assert is_jwt_format("a.b") is False
    assert is_jwt_format("a.b.c.d") is False
    assert is_jwt_format("plain") is False
