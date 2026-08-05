"""The configuration contract for gateway principal verification.

The signing key is a credential, so it comes through ``SecretResolver`` under the
name the ``secret_names`` config registers — not off the environment. The
composition root resolves it once at boot and pushes it in, because the consumer
runs in ASGI middleware outside the injector.

These tests are the record of that wiring, of ``aud``/``iss`` being fixed in code
rather than configurable, and of the deliberate choice to deny rather than invent
a fallback key: every way the key can fail to resolve ends in an empty key, and
the verifier answers 401 to everything on an empty key.
"""

from __future__ import annotations

import pytest

from agentclaw.community.plugin_api.secret_resolver import SecretResolver
from agentclaw.community.utils.gateway_principal_config import (
    get_principal_verifier_config,
    init_principal_verifier_config,
    reset_principal_verifier_config_cache,
)

SECRET_NAME = "the-registered-secret-name"
KEY = "a-shared-secret-of-at-least-32-bytes!!"


class _Secret:
    """The ``.secret_user`` / ``.secret_value`` shape every resolver returns."""

    def __init__(self, value: str) -> None:
        self.secret_user = "gateway"
        self.secret_value = value


class _FakeResolver(SecretResolver):
    """Records what it was asked for, and answers however the test wants."""

    def __init__(self, secret: object | None = None, raises: bool = False) -> None:
        self._secret = secret
        self._raises = raises
        self.calls: list[str] = []

    def get_secret(self, secret_name: str) -> object | None:
        self.calls.append(secret_name)
        if self._raises:
            raise RuntimeError("secret store unreachable")
        return self._secret


@pytest.fixture(autouse=True)
def clear_config():
    reset_principal_verifier_config_cache()
    yield
    reset_principal_verifier_config_cache()


def test_key_is_resolved_through_the_secret_resolver():
    resolver = _FakeResolver(_Secret(KEY))

    init_principal_verifier_config(resolver, SECRET_NAME, strict=False)

    assert get_principal_verifier_config().signing_key == KEY
    assert resolver.calls == [SECRET_NAME], "looked the secret up by its registered name"


def test_audience_and_issuer_are_fixed_in_code():
    """Two ends of one wire contract — the gateway makes neither configurable.

    It signs ``iss`` from a hardcoded default and ``aud`` from this component's
    name under ``servers:`` in its own config. A knob on only the verifying side
    could not change the contract, just break it.
    """
    init_principal_verifier_config(_FakeResolver(_Secret(KEY)), SECRET_NAME, strict=False)

    config = get_principal_verifier_config()

    assert config.audience == "backend"
    assert config.issuer == "gateway"


def test_before_boot_the_config_denies():
    """A request arriving before init must deny, not crash."""
    assert get_principal_verifier_config().signing_key == ""


def test_no_registered_secret_name_denies():
    """A deployment that never registered the name has no key — so it denies."""
    resolver = _FakeResolver(_Secret(KEY))

    init_principal_verifier_config(resolver, "", strict=False)

    assert get_principal_verifier_config().signing_key == ""
    assert resolver.calls == [], "never looked up a secret it had no name for"


def test_absent_secret_denies():
    """Registered, but the store has no such secret."""
    init_principal_verifier_config(_FakeResolver(None), SECRET_NAME, strict=False)

    assert get_principal_verifier_config().signing_key == ""


@pytest.mark.parametrize("value", ["", "   "])
def test_empty_or_whitespace_value_counts_as_unset(value):
    """A key that is empty or accidentally whitespace must not look configured."""
    init_principal_verifier_config(_FakeResolver(_Secret(value)), SECRET_NAME, strict=False)

    assert get_principal_verifier_config().signing_key == ""


def test_resolver_failure_denies_instead_of_raising():
    """Permissive: a secret-store outage must not stop a local boot.

    Without the key we cannot tell a gateway token from a forged one, which is
    the same answer as never having had one: deny.
    """
    init_principal_verifier_config(_FakeResolver(raises=True), SECRET_NAME, strict=False)

    assert get_principal_verifier_config().signing_key == ""


# ── strict environments refuse to boot without a key ─────────────────────────


@pytest.mark.parametrize(
    "resolver,secret_name",
    [
        pytest.param(_FakeResolver(_Secret(KEY)), "", id="no-name-registered"),
        pytest.param(_FakeResolver(None), SECRET_NAME, id="secret-absent"),
        pytest.param(_FakeResolver(_Secret("")), SECRET_NAME, id="empty-value"),
        pytest.param(_FakeResolver(_Secret("   ")), SECRET_NAME, id="whitespace-value"),
        pytest.param(_FakeResolver(raises=True), SECRET_NAME, id="store-unreachable"),
    ],
)
def test_strict_boot_fails_when_no_key_resolves(resolver, secret_name):
    """Serving the public API without a key is broken, not degraded.

    Such a deployment answers 401 to every /openapi/v1 request while looking
    healthy, so pre/prod must fail the rollout rather than surface it as a
    support ticket. Every way the key can fail to resolve raises here.
    """
    with pytest.raises(RuntimeError, match="no signing key"):
        init_principal_verifier_config(resolver, secret_name, strict=True)


def test_strict_boot_succeeds_with_a_key():
    init_principal_verifier_config(_FakeResolver(_Secret(KEY)), SECRET_NAME, strict=True)

    assert get_principal_verifier_config().signing_key == KEY


def test_strict_failure_leaves_the_config_denying():
    """If a caller swallows the boot error, the surface must still not open."""
    with pytest.raises(RuntimeError):
        init_principal_verifier_config(_FakeResolver(None), SECRET_NAME, strict=True)

    assert get_principal_verifier_config().signing_key == ""


def test_no_fallback_key_is_invented():
    """The gateway's ``bare`` signer keeps a dev fallback; we deliberately do not.

    A committed shared secret is a committed credential, and here "no key" fails
    safe rather than open.
    """
    init_principal_verifier_config(_FakeResolver(None), SECRET_NAME, strict=False)

    assert get_principal_verifier_config().signing_key == ""


def test_key_is_resolved_once_at_boot():
    """Deployment config, not per-request state — no secret-store round trip on
    the hot path, and (by the same token) no rotation without a restart."""
    resolver = _FakeResolver(_Secret(KEY))
    init_principal_verifier_config(resolver, SECRET_NAME, strict=False)

    first = get_principal_verifier_config()

    assert get_principal_verifier_config() is first
    assert resolver.calls == [SECRET_NAME], "resolved once at boot, then reused"
