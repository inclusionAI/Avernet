"""Unit tests for the auth runner (OR/AND + scope, §7)."""

from __future__ import annotations

import pytest

from gateway.community.core.authn import authenticate
from gateway.community.spi.auth import AuthenticatedUser, AuthError
from gateway.community.spi.authn import (
    AuthStrategy,
    CredentialBundle,
    Principal,
    StrategyParams,
    UserPrincipal,
)

_CREDS = CredentialBundle(headers={}, cookies={}, query={})


def _principal(scopes: frozenset[str] = frozenset()) -> UserPrincipal:
    return UserPrincipal(
        tenant="t", subject=AuthenticatedUser(id="u", username="a"), scopes=scopes
    )


class _Fixed:
    """A strategy that always yields a fixed result (Principal, None, or raises)."""

    def __init__(self, name: str, result: Principal | AuthError | None) -> None:
        self.name = name
        self._result = result

    async def build(
        self, creds: CredentialBundle, params: StrategyParams
    ) -> Principal | None:
        if isinstance(self._result, AuthError):
            raise self._result
        return self._result


async def test_returns_principal_on_success() -> None:
    registry: dict[str, AuthStrategy] = {"fp": _Fixed("fp", _principal())}
    principal = await authenticate(_CREDS, [{"fp": StrategyParams()}], registry)
    assert isinstance(principal, UserPrincipal)


async def test_none_result_is_unauthorized() -> None:
    registry: dict[str, AuthStrategy] = {"fp": _Fixed("fp", None)}
    with pytest.raises(AuthError):
        await authenticate(_CREDS, [{"fp": StrategyParams()}], registry)


async def test_autherror_propagates() -> None:
    registry: dict[str, AuthStrategy] = {"fp": _Fixed("fp", AuthError("bad"))}
    with pytest.raises(AuthError):
        await authenticate(_CREDS, [{"fp": StrategyParams()}], registry)


async def test_insufficient_scope_is_denied() -> None:
    registry: dict[str, AuthStrategy] = {"fp": _Fixed("fp", _principal())}
    req = [{"fp": StrategyParams(scopes=frozenset({"bots:read"}))}]
    with pytest.raises(AuthError):
        await authenticate(_CREDS, req, registry)


async def test_required_scope_subset_is_allowed() -> None:
    registry: dict[str, AuthStrategy] = {
        "fp": _Fixed("fp", _principal(scopes=frozenset({"bots:read", "bots:write"})))
    }
    req = [{"fp": StrategyParams(scopes=frozenset({"bots:read"}))}]
    principal = await authenticate(_CREDS, req, registry)
    assert isinstance(principal, UserPrincipal)


async def test_or_falls_back_to_second_alternative() -> None:
    registry: dict[str, AuthStrategy] = {
        "a": _Fixed("a", None),
        "b": _Fixed("b", _principal()),
    }
    req = [{"a": StrategyParams()}, {"b": StrategyParams()}]
    principal = await authenticate(_CREDS, req, registry)
    assert isinstance(principal, UserPrincipal)


async def test_unknown_strategy_is_denied() -> None:
    with pytest.raises(AuthError):
        await authenticate(_CREDS, [{"missing": StrategyParams()}], {})


async def test_invalid_credential_is_terminal_no_fallback() -> None:
    # A present-but-invalid credential (AuthError) in one alternative must NOT
    # fall back to a later valid alternative — the whole attempt is rejected.
    registry: dict[str, AuthStrategy] = {
        "bad": _Fixed("bad", AuthError("invalid api key")),
        "good": _Fixed("good", _principal()),
    }
    req = [{"bad": StrategyParams()}, {"good": StrategyParams()}]
    with pytest.raises(AuthError):
        await authenticate(_CREDS, req, registry)
