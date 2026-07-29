"""Unit tests for the ``access_key_token`` strategy (token → AccessKeyRegistry → AccessKeyPrincipal).

Uses a tiny in-test fake registry (no DB) so the strategy's extraction logic is
exercised in isolation; the DB-backed registry has its own test.
"""

from __future__ import annotations

from datetime import datetime

from gateway.community.plugins.authn.access_key_token import AccessKeyTokenStrategy
from gateway.community.spi.access_key import RegisteredAccessKey
from gateway.community.spi.authn import AccessKeyPrincipal, CredentialBundle

_AK_EXPIRE = datetime(2027, 1, 1, 0, 0, 0)


class _FakeAccessKeyRegistry:
    """Resolves only ``ak-token`` → a fixed RegisteredAccessKey; else None."""

    _AK = RegisteredAccessKey(access_key_id="ak-1", tenant="t", expire_at=_AK_EXPIRE)

    async def find_access_key_by_token(self, token: str) -> RegisteredAccessKey | None:
        return self._AK if token == "ak-token" else None


def _strat() -> AccessKeyTokenStrategy:
    return AccessKeyTokenStrategy(registry=_FakeAccessKeyRegistry())


def _creds(headers: dict[str, str]) -> CredentialBundle:
    return CredentialBundle(headers=headers, cookies={}, query={})


async def test_absent_token_returns_none() -> None:
    assert await _strat().build(_creds({})) is None


async def test_empty_token_returns_none() -> None:
    assert await _strat().build(_creds({"x-avernet-access-key-token": "  "})) is None


async def test_unknown_token_returns_none() -> None:
    # An unrecognized token is a soft miss → None (not applicable).
    result = await _strat().build(_creds({"x-avernet-access-key-token": "nope"}))
    assert result is None


async def test_valid_token_builds_access_key_principal() -> None:
    result = await _strat().build(_creds({"x-avernet-access-key-token": "ak-token"}))
    assert isinstance(result, AccessKeyPrincipal)
    assert result.tenant == "t"
    assert result.access_key.access_key_id == "ak-1"
    assert result.access_key.access_key_token == "ak-token"
    assert result.access_key.expire_at == _AK_EXPIRE
