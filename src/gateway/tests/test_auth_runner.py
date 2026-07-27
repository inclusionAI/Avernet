"""Unit tests for the auth runner (ordered fallback per identity type, rev 3)."""

from __future__ import annotations

import pytest

from gateway.community.core.authn import Identities, authenticate
from gateway.community.spi.auth import AuthenticatedUser, AuthError
from gateway.community.spi.authn import (
    AuthStrategy,
    BotPrincipal,
    CredentialBundle,
    Principal,
    PrincipalType,
    UserPrincipal,
)

_CREDS = CredentialBundle(headers={}, cookies={}, query={})


def _user() -> UserPrincipal:
    return UserPrincipal(tenant="t", subject=AuthenticatedUser(id="u", username="a"))


def _bot() -> BotPrincipal:
    return BotPrincipal(tenant="t", bot_uuid="b", owner_id="o", token="k")


class _Fixed:
    """A strategy that always yields a fixed result (Principal, None, or raises)."""

    def __init__(
        self, name: str, ptype: PrincipalType, result: Principal | AuthError | None
    ) -> None:
        self.name = name
        self.principal_type = ptype
        self._result = result

    async def build(self, creds: CredentialBundle) -> Principal | None:
        if isinstance(self._result, AuthError):
            raise self._result
        return self._result


async def test_single_type_returns_principal() -> None:
    registry: dict[PrincipalType, tuple[AuthStrategy, ...]] = {
        PrincipalType.USER: (_Fixed("fp", PrincipalType.USER, _user()),)
    }
    ids = await authenticate(_CREDS, frozenset({PrincipalType.USER}), registry)
    assert isinstance(ids.require(PrincipalType.USER), UserPrincipal)


async def test_missing_credential_is_unauthorized() -> None:
    registry: dict[PrincipalType, tuple[AuthStrategy, ...]] = {
        PrincipalType.USER: (_Fixed("fp", PrincipalType.USER, None),)
    }
    with pytest.raises(AuthError):
        await authenticate(_CREDS, frozenset({PrincipalType.USER}), registry)


async def test_invalid_credential_is_terminal_no_fallback() -> None:
    # A present-but-invalid credential (AuthError) must NOT fall back to a later
    # plugin — the whole type attempt is rejected.
    registry: dict[PrincipalType, tuple[AuthStrategy, ...]] = {
        PrincipalType.USER: (
            _Fixed("bad", PrincipalType.USER, AuthError("invalid")),
            _Fixed("good", PrincipalType.USER, _user()),
        )
    }
    with pytest.raises(AuthError):
        await authenticate(_CREDS, frozenset({PrincipalType.USER}), registry)


async def test_none_falls_through_to_next_plugin() -> None:
    registry: dict[PrincipalType, tuple[AuthStrategy, ...]] = {
        PrincipalType.USER: (
            _Fixed("a", PrincipalType.USER, None),
            _Fixed("b", PrincipalType.USER, _user()),
        )
    }
    ids = await authenticate(_CREDS, frozenset({PrincipalType.USER}), registry)
    assert isinstance(ids.require(PrincipalType.USER), UserPrincipal)


async def test_chain_exhausted_is_unauthorized() -> None:
    registry: dict[PrincipalType, tuple[AuthStrategy, ...]] = {
        PrincipalType.USER: (
            _Fixed("a", PrincipalType.USER, None),
            _Fixed("b", PrincipalType.USER, None),
        )
    }
    with pytest.raises(AuthError):
        await authenticate(_CREDS, frozenset({PrincipalType.USER}), registry)


async def test_multiple_required_types_all_collected() -> None:
    registry: dict[PrincipalType, tuple[AuthStrategy, ...]] = {
        PrincipalType.USER: (_Fixed("u", PrincipalType.USER, _user()),),
        PrincipalType.BOT: (_Fixed("bb", PrincipalType.BOT, _bot()),),
    }
    ids = await authenticate(
        _CREDS, frozenset({PrincipalType.USER, PrincipalType.BOT}), registry
    )
    assert isinstance(ids.require(PrincipalType.USER), UserPrincipal)
    assert isinstance(ids.require(PrincipalType.BOT), BotPrincipal)


async def test_missing_one_required_type_rejects() -> None:
    registry: dict[PrincipalType, tuple[AuthStrategy, ...]] = {
        PrincipalType.USER: (_Fixed("u", PrincipalType.USER, _user()),),
        PrincipalType.BOT: (_Fixed("bb", PrincipalType.BOT, None),),
    }
    with pytest.raises(AuthError):
        await authenticate(
            _CREDS, frozenset({PrincipalType.USER, PrincipalType.BOT}), registry
        )


async def test_unknown_type_in_requirement_is_denied() -> None:
    registry: dict[PrincipalType, tuple[AuthStrategy, ...]] = {}
    with pytest.raises(AuthError):
        await authenticate(_CREDS, frozenset({PrincipalType.USER}), registry)
