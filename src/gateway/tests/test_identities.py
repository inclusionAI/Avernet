"""Unit tests for the Identities container."""

from __future__ import annotations

import pytest

from gateway.community.core.authn import Identities
from gateway.community.spi.auth import AuthenticatedUser
from gateway.community.spi.authn import BotPrincipal, PrincipalType, UserPrincipal

_USER = UserPrincipal(tenant="t", subject=AuthenticatedUser(id="u", username="a"))
_BOT = BotPrincipal(tenant="t", bot_uuid="b", owner_id="o", token="k")


def test_get_returns_present_principal() -> None:
    ids = Identities({PrincipalType.USER: _USER})
    assert ids.get(PrincipalType.USER) is _USER


def test_get_returns_none_for_absent() -> None:
    assert Identities({}).get(PrincipalType.BOT) is None


def test_require_returns_present_principal() -> None:
    assert Identities({PrincipalType.USER: _USER}).require(PrincipalType.USER) is _USER


def test_require_raises_for_absent() -> None:
    with pytest.raises(KeyError):
        Identities({}).require(PrincipalType.USER)


def test_iter_yields_present_types() -> None:
    ids = Identities({PrincipalType.USER: _USER, PrincipalType.BOT: _BOT})
    assert set(ids) == {PrincipalType.USER, PrincipalType.BOT}


def test_identities_is_frozen() -> None:
    ids = Identities({PrincipalType.USER: _USER})
    with pytest.raises(Exception):
        ids._principals = {}  # type: ignore[misc]
