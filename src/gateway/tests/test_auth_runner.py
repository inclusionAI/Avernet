"""Unit tests for the auth runner (per-identity resolution) + the IdentityChain."""

from __future__ import annotations

import pytest

from gateway.community.core.authn import IdentityChain, authenticate
from gateway.community.spi.auth import AuthenticatedUser, AuthError
from gateway.community.spi.authn import (
    AppPrincipal,
    AuthStrategy,
    CredentialBundle,
    Presence,
    Principal,
    PrincipalType,
    ThirdPartyApp,
    UserPrincipal,
)

_CREDS = CredentialBundle(headers={}, cookies={}, query={})


def _user_p() -> UserPrincipal:
    return UserPrincipal(tenant="t", subject=AuthenticatedUser(id="u", username="a"))


def _app_p() -> AppPrincipal:
    return AppPrincipal(
        tenant="t",
        app=ThirdPartyApp(app_id="c", app_name="C App", owners="o", tenant="t"),
    )


class _Fixed:
    """A strategy stub returning a fixed Principal / None / raising AuthError."""

    def __init__(
        self,
        name: str,
        result: Principal | AuthError | None,
        *,
        principal_type: PrincipalType = PrincipalType.USER,
    ) -> None:
        self.name = name
        self.principal_type = principal_type
        self._result = result

    async def build(self, creds: CredentialBundle) -> Principal | None:
        if isinstance(self._result, AuthError):
            raise self._result
        return self._result


def _req(**presences: Presence) -> dict[PrincipalType, Presence]:
    return {PrincipalType(k): v for k, v in presences.items()}


def _chain(ptype: PrincipalType, *strategies: AuthStrategy) -> IdentityChain:
    return IdentityChain(ptype, tuple(strategies))


# ── IdentityChain ────────────────────────────────────────────────────────────


async def test_chain_first_success_wins() -> None:
    chain = _chain(PrincipalType.USER, _Fixed("a", _user_p()), _Fixed("b", None))
    assert isinstance(await chain.build(_CREDS), UserPrincipal)


async def test_chain_skips_none_and_returns_later_principal() -> None:
    chain = _chain(PrincipalType.APP, _Fixed("a", None), _Fixed("b", _app_p()))
    assert isinstance(await chain.build(_CREDS), AppPrincipal)


async def test_chain_all_none_returns_none() -> None:
    chain = _chain(PrincipalType.USER, _Fixed("a", None), _Fixed("b", None))
    assert await chain.build(_CREDS) is None


async def test_chain_hard_failure_propagates_and_does_not_fall_back() -> None:
    chain = _chain(
        PrincipalType.USER, _Fixed("bad", AuthError("bad")), _Fixed("good", _user_p())
    )
    with pytest.raises(AuthError):
        await chain.build(_CREDS)


async def test_chain_empty_returns_none() -> None:
    chain = _chain(PrincipalType.USER)
    assert await chain.build(_CREDS) is None


async def test_chain_wrong_principal_type_raises() -> None:
    # Inner strategy returns a principal of the wrong type → defensive guard.
    chain = _chain(
        PrincipalType.USER, _Fixed("user", _app_p(), principal_type=PrincipalType.USER)
    )
    with pytest.raises(AuthError):
        await chain.build(_CREDS)


def test_chain_satisfies_auth_strategy_shape() -> None:
    # The chain carries the AuthStrategy surface (name + principal_type + build).
    chain = _chain(PrincipalType.BOT)
    assert chain.name == "bot"
    assert chain.principal_type is PrincipalType.BOT
    assert callable(chain.build)


# ── runner ───────────────────────────────────────────────────────────────────


async def test_required_identity_present_returns_it() -> None:
    reg: dict[PrincipalType, IdentityChain] = {
        PrincipalType.USER: _chain(PrincipalType.USER, _Fixed("user", _user_p()))
    }
    result = await authenticate(_CREDS, _req(user=Presence.REQUIRED), reg)
    assert isinstance(result[PrincipalType.USER], UserPrincipal)


async def test_required_identity_absent_denies() -> None:
    reg: dict[PrincipalType, IdentityChain] = {
        PrincipalType.USER: _chain(PrincipalType.USER, _Fixed("user", None))
    }
    with pytest.raises(AuthError):
        await authenticate(_CREDS, _req(user=Presence.REQUIRED), reg)


async def test_optional_identity_absent_is_skipped() -> None:
    reg: dict[PrincipalType, IdentityChain] = {
        PrincipalType.APP: _chain(
            PrincipalType.APP, _Fixed("app", None, principal_type=PrincipalType.APP)
        )
    }
    result = await authenticate(_CREDS, _req(app=Presence.OPTIONAL), reg)
    assert PrincipalType.APP not in result


async def test_optional_identity_present_is_included() -> None:
    reg: dict[PrincipalType, IdentityChain] = {
        PrincipalType.APP: _chain(
            PrincipalType.APP,
            _Fixed("app", _app_p(), principal_type=PrincipalType.APP),
        )
    }
    result = await authenticate(_CREDS, _req(app=Presence.OPTIONAL), reg)
    assert isinstance(result[PrincipalType.APP], AppPrincipal)


async def test_hard_failure_is_terminal() -> None:
    reg: dict[PrincipalType, IdentityChain] = {
        PrincipalType.USER: _chain(PrincipalType.USER, _Fixed("user", AuthError("bad")))
    }
    with pytest.raises(AuthError):
        await authenticate(_CREDS, _req(user=Presence.REQUIRED), reg)


async def test_optional_hard_failure_is_still_terminal() -> None:
    reg: dict[PrincipalType, IdentityChain] = {
        PrincipalType.APP: _chain(
            PrincipalType.APP,
            _Fixed("app", AuthError("bad"), principal_type=PrincipalType.APP),
        )
    }
    with pytest.raises(AuthError):
        await authenticate(_CREDS, _req(app=Presence.OPTIONAL), reg)


async def test_multiple_identities_coexist() -> None:
    reg: dict[PrincipalType, IdentityChain] = {
        PrincipalType.USER: _chain(PrincipalType.USER, _Fixed("user", _user_p())),
        PrincipalType.APP: _chain(
            PrincipalType.APP,
            _Fixed("app", _app_p(), principal_type=PrincipalType.APP),
        ),
    }
    result = await authenticate(
        _CREDS,
        _req(user=Presence.REQUIRED, app=Presence.OPTIONAL),
        reg,
    )
    assert isinstance(result[PrincipalType.USER], UserPrincipal)
    assert isinstance(result[PrincipalType.APP], AppPrincipal)


async def test_unknown_identity_denies() -> None:
    # Empty registry: requirement asks for "user" but no chain is registered
    # for it → misconfig, terminal AuthError.
    reg: dict[PrincipalType, IdentityChain] = {}
    with pytest.raises(AuthError):
        await authenticate(_CREDS, _req(user=Presence.REQUIRED), reg)


async def test_optional_exhausted_chain_is_skipped() -> None:
    # Two strategies, both inapplicable (None) → chain exhausted → OPTIONAL skips.
    reg: dict[PrincipalType, IdentityChain] = {
        PrincipalType.APP: _chain(
            PrincipalType.APP,
            _Fixed("a", None, principal_type=PrincipalType.APP),
            _Fixed("b", None, principal_type=PrincipalType.APP),
        )
    }
    result = await authenticate(_CREDS, _req(app=Presence.OPTIONAL), reg)
    assert PrincipalType.APP not in result


async def test_required_exhausted_chain_raises() -> None:
    # REQUIRED + exhausted → AuthError (unauthenticated: no credential).
    reg: dict[PrincipalType, IdentityChain] = {
        PrincipalType.APP: _chain(
            PrincipalType.APP,
            _Fixed("a", None, principal_type=PrincipalType.APP),
            _Fixed("b", None, principal_type=PrincipalType.APP),
        )
    }
    with pytest.raises(AuthError):
        await authenticate(_CREDS, _req(app=Presence.REQUIRED), reg)


async def test_runner_wrong_principal_type_raises() -> None:
    # The chain's own type-guard raises before the runner sees the principal.
    reg: dict[PrincipalType, IdentityChain] = {
        PrincipalType.USER: _chain(
            PrincipalType.USER,
            _Fixed("user", _app_p(), principal_type=PrincipalType.USER),
        )
    }
    with pytest.raises(AuthError):
        await authenticate(_CREDS, _req(user=Presence.REQUIRED), reg)


async def test_runner_chain_falls_through_first_none_to_second() -> None:
    # First strategy None, second returns the principal → success.
    reg: dict[PrincipalType, IdentityChain] = {
        PrincipalType.APP: _chain(
            PrincipalType.APP,
            _Fixed("a", None, principal_type=PrincipalType.APP),
            _Fixed("b", _app_p(), principal_type=PrincipalType.APP),
        )
    }
    result = await authenticate(_CREDS, _req(app=Presence.REQUIRED), reg)
    assert isinstance(result[PrincipalType.APP], AppPrincipal)


async def test_runner_hard_failure_in_chain_does_not_fall_through() -> None:
    # First strategy raises AuthError (present-but-invalid) → terminal; the
    # second strategy must NOT be reached.
    reached = False

    class _Reach:
        name = "b"
        principal_type = PrincipalType.APP

        async def build(self, creds: CredentialBundle) -> Principal | None:
            nonlocal reached
            reached = True
            return _app_p()

    reg: dict[PrincipalType, IdentityChain] = {
        PrincipalType.APP: _chain(
            PrincipalType.APP,
            _Fixed("a", AuthError("bad"), principal_type=PrincipalType.APP),
            _Reach(),
        )
    }
    with pytest.raises(AuthError):
        await authenticate(_CREDS, _req(app=Presence.OPTIONAL), reg)
    assert reached is False
