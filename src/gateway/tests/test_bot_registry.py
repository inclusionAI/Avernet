"""Tests for the in-memory bot registry + its lookup in BotTokenStrategy.

The registry exposes only ``find_bot_by_token``; the ``bare`` flavor seeds it
with a demo bot.
"""

from __future__ import annotations

from gateway.community.plugins.authn.bot_token import (
    Bot,
    BotTokenStrategy,
    InMemoryBotRegistry,
)
from gateway.community.spi.authn import BotPrincipal, CredentialBundle

_DEDICATED = "x-bot-token"


def _registry(entries: dict[str, Bot] | None = None) -> InMemoryBotRegistry:
    return InMemoryBotRegistry(entries=entries)


def _creds(headers: dict[str, str] | None = None) -> CredentialBundle:
    return CredentialBundle(headers=headers or {}, cookies={}, query={})


def _strategy(registry: InMemoryBotRegistry | None = None) -> BotTokenStrategy:
    return BotTokenStrategy(
        registry=registry or InMemoryBotRegistry(), token_header=_DEDICATED
    )


# ── InMemoryBotRegistry ──────────────────────────────────────────────────────


async def test_seeded_demo_bot_resolves_in_one_lookup() -> None:
    bot = await InMemoryBotRegistry().find_bot_by_token("bot-key")
    assert bot is not None
    assert bot.bot_uuid == "bot-7"
    assert bot.owner_id == "owner-1"
    assert bot.tenant == "t"


async def test_unknown_token_returns_none() -> None:
    assert await InMemoryBotRegistry().find_bot_by_token("unknown") is None


async def test_empty_token_returns_none() -> None:
    assert await InMemoryBotRegistry().find_bot_by_token("") is None


async def test_custom_entries_resolve() -> None:
    reg = _registry(
        entries={
            "tok-42": Bot(bot_uuid="bot-42", owner_id="owner-9", tenant="tenant-x")
        }
    )
    assert await reg.find_bot_by_token("tok-42") == Bot(
        bot_uuid="bot-42", owner_id="owner-9", tenant="tenant-x"
    )


async def test_explicit_entries_replaces_default_seed() -> None:
    # Passing entries does NOT seed the demo bot.
    reg = _registry(entries={"only": Bot(bot_uuid="b", owner_id="o", tenant="t")})
    assert await reg.find_bot_by_token("bot-key") is None
    assert (await reg.find_bot_by_token("only")).bot_uuid == "b"


# ── BotTokenStrategy consumes the registry ────────────────────────────────────


async def test_strategy_resolves_token_to_principal() -> None:
    principal = await _strategy().build(_creds({_DEDICATED: "bot-key"}))
    assert isinstance(principal, BotPrincipal)
    assert principal.bot_uuid == "bot-7"
    assert principal.owner_id == "owner-1"
    assert principal.tenant == "t"
    assert principal.token == "bot-key"


async def test_strategy_unknown_token_soft_miss() -> None:
    assert await _strategy().build(_creds({_DEDICATED: "nope"})) is None


async def test_strategy_empty_token_soft_miss() -> None:
    assert await _strategy().build(_creds({_DEDICATED: ""})) is None


async def test_strategy_no_token_soft_miss() -> None:
    assert await _strategy().build(_creds()) is None


async def test_strategy_resolves_from_custom_registry() -> None:
    reg = _registry(entries={"tok": Bot(bot_uuid="b", owner_id="o", tenant="t")})
    principal = await _strategy(reg).build(_creds({_DEDICATED: "tok"}))
    assert isinstance(principal, BotPrincipal)
    assert principal.bot_uuid == "b"
    assert principal.owner_id == "o"
