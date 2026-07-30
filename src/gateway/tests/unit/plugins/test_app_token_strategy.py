"""Unit tests for the ``app_token`` strategy (``x-avernet-app-token`` / Bearer → AppPrincipal).

Uses a tiny in-test fake registry (no DB) so the strategy's extraction /
adjudication logic is exercised in isolation; the DB-backed registry has its
own test (``test_app_registry_db.py``).
"""

from __future__ import annotations

from gateway.community.plugins.authn.app_token import AppTokenStrategy
from gateway.community.spi.app import RegisteredApp
from gateway.community.spi.authn import AppPrincipal, CredentialBundle

_APP_HEADER = "x-avernet-app-token"


class _FakeAppRegistry:
    """Resolves only ``app-key`` → a fixed RegisteredApp; else None (soft miss)."""

    _APP = RegisteredApp(
        id=1,
        app_name="Demo App",
        owners="org-1",
        app_type="assistant",
        tenant="t",
    )

    async def find_app_by_token(self, token: str) -> RegisteredApp | None:
        return self._APP if token == "app-key" else None


def _strat() -> AppTokenStrategy:
    return AppTokenStrategy(registry=_FakeAppRegistry())


def _creds(headers: dict[str, str]) -> CredentialBundle:
    return CredentialBundle(headers=headers, cookies={}, query={})


async def test_absent_token_returns_none() -> None:
    assert await _strat().build(_creds({})) is None


async def test_dedicated_header_resolves() -> None:
    result = await _strat().build(_creds({_APP_HEADER: "app-key"}))
    assert isinstance(result, AppPrincipal)
    assert result.tenant == "t"
    assert result.app.app_id == 1
    assert result.app.tenant == "t"


async def test_bearer_fallback_resolves() -> None:
    result = await _strat().build(_creds({"authorization": "Bearer app-key"}))
    assert isinstance(result, AppPrincipal)
    assert result.app.app_id == 1


async def test_dedicated_header_wins_over_bearer() -> None:
    # The dedicated header wins; the Bearer is not consulted.
    result = await _strat().build(
        _creds({_APP_HEADER: "app-key", "authorization": "Bearer nope"})
    )
    assert isinstance(result, AppPrincipal)
    assert result.app.app_id == 1


async def test_empty_dedicated_header_falls_back_to_bearer() -> None:
    result = await _strat().build(
        _creds({_APP_HEADER: "   ", "authorization": "Bearer app-key"})
    )
    assert isinstance(result, AppPrincipal)


async def test_unrecognized_token_returns_none() -> None:
    # An unrecognized token is "not one of mine" → absent (US27).
    assert await _strat().build(_creds({_APP_HEADER: "nope"})) is None


async def test_bot_bearer_is_absent_for_app_chain_us27() -> None:
    # US27: a bot session token presented as a Bearer must NOT be treated as an
    # invalid app token by the app chain — the app strategy returns None
    # (absent, registry miss), letting a bot chain resolve the same credential.
    assert await _strat().build(_creds({"authorization": "Bearer bot-key"})) is None


async def test_jwt_shaped_bearer_is_absent_for_app_chain() -> None:
    # A JWT-shaped Bearer is not a recognised app token; the registry finds no
    # match → None (US27 holds).
    assert await _strat().build(_creds({"authorization": "Bearer a.b.c"})) is None


async def test_non_bearer_authorization_is_not_an_app_token() -> None:
    # A bare (non-Bearer) Authorization value is not accepted by the app fallback.
    assert await _strat().build(_creds({"authorization": "app-key"})) is None
