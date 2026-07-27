"""Unit tests for the bot_token strategy (mirrors BCS SessionTokenPlugin cases)."""

from __future__ import annotations

from gateway.community.plugins.authn.bot_token import (
    BotTokenStrategy,
    InMemoryBotRegistry,
)
from gateway.community.plugins.authn.bot_token._strategy import (
    extract_bot_token,
    is_jwt_format,
)
from gateway.community.spi.authn import (
    BotPrincipal,
    CredentialBundle,
    PrincipalType,
)

_DEDICATED = "x-bot-token"


def _strategy() -> BotTokenStrategy:
    return BotTokenStrategy(registry=InMemoryBotRegistry(), token_header=_DEDICATED)


def _creds(headers: dict[str, str] | None = None) -> CredentialBundle:
    return CredentialBundle(headers=headers or {}, cookies={}, query={})


# ── is_jwt_format ─────────────────────────────────────────────────────────────


def test_is_jwt_format_matches_three_segments() -> None:
    assert is_jwt_format("eyJraWQ.eyJhdWQ.NI1Swo")
    assert is_jwt_format("a.b.c")


def test_is_jwt_format_rejects_non_jwt() -> None:
    assert not is_jwt_format("bot-key")
    assert not is_jwt_format("00000000-0000-0000-0000-000000000001")
    assert not is_jwt_format("")
    assert not is_jwt_format("a.b")  # only two segments
    assert not is_jwt_format("a.b.c.d")  # four segments


# ── extract_bot_token ─────────────────────────────────────────────────────────


def test_extract_returns_none_when_no_header() -> None:
    assert extract_bot_token(_creds(), _DEDICATED) is None


def test_extract_dedicated_header_wins_over_bearer() -> None:
    creds = _creds({_DEDICATED: "raw-token", "authorization": "Bearer other"})
    assert extract_bot_token(creds, _DEDICATED) == "raw-token"


def test_extract_uses_non_jwt_bearer_when_no_dedicated() -> None:
    creds = _creds({"authorization": "Bearer 00000000-0000-0000-0000-000000000001"})
    assert (
        extract_bot_token(creds, _DEDICATED) == "00000000-0000-0000-0000-000000000001"
    )


def test_extract_ignores_jwt_bearer() -> None:
    # A JWT-shaped bearer is left for a JWT strategy, not treated as a bot token.
    creds = _creds({"authorization": "Bearer eyJh.eyJz.sig"})
    assert extract_bot_token(creds, _DEDICATED) is None


def test_extract_ignores_empty_values() -> None:
    assert extract_bot_token(_creds({_DEDICATED: "  "}), _DEDICATED) is None
    assert extract_bot_token(_creds({"authorization": "Bearer "}), _DEDICATED) is None


# ── BotTokenStrategy.build ────────────────────────────────────────────────────


async def test_declares_bot_type() -> None:
    assert _strategy().principal_type is PrincipalType.BOT


async def test_returns_none_when_no_bot_token() -> None:
    assert await _strategy().build(_creds()) is None


async def test_ignores_jwt_bearer_token() -> None:
    # A JWT bearer is not a bot token → not applicable (None).
    creds = _creds({"authorization": "Bearer eyJh.eyJz.sig"})
    assert await _strategy().build(creds) is None


async def test_builds_bot_principal_from_dedicated_header() -> None:
    principal = await _strategy().build(_creds({_DEDICATED: "bot-key"}))
    assert isinstance(principal, BotPrincipal)
    assert principal.bot_uuid == "bot-7"
    assert principal.owner_id == "owner-1"
    assert principal.tenant == "t"
    assert principal.token == "bot-key"


async def test_builds_bot_principal_from_non_jwt_bearer() -> None:
    creds = _creds({"authorization": "Bearer bot-key"})
    principal = await _strategy().build(creds)
    assert isinstance(principal, BotPrincipal)
    assert principal.bot_uuid == "bot-7"
    assert principal.token == "bot-key"


async def test_dedicated_header_wins_over_bearer() -> None:
    # Dedicated "bot-key" resolves; the bearer "bad" would not — dedicated wins.
    creds = _creds({_DEDICATED: "bot-key", "authorization": "Bearer bad"})
    principal = await _strategy().build(creds)
    assert isinstance(principal, BotPrincipal)
    assert principal.bot_uuid == "bot-7"


async def test_unknown_token_returns_none_soft_miss() -> None:
    # Mirrors BCS: an unknown token is a soft miss (None), not a hard failure,
    # so the chain may continue / fail-close.
    assert await _strategy().build(_creds({_DEDICATED: "unknown"})) is None


async def test_unknown_bearer_returns_none_soft_miss() -> None:
    creds = _creds({"authorization": "Bearer unknown"})
    assert await _strategy().build(creds) is None
