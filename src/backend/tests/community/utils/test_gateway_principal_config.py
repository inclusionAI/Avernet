"""The configuration contract for gateway principal verification.

The signing key is a credential, so it comes through ``SecretResolver`` under the
name the ``secret_names`` config registers — not off the environment. The
composition root resolves it once at boot and pushes it in, because the consumer
runs in ASGI middleware outside the injector.

These tests are the record of that wiring, of ``aud``/``iss`` being fixed in code
rather than configurable, and of the deliberate choice to deny rather than invent
a fallback key: every way the key can fail to resolve ends in an empty key, and
the verifier answers 401 to everything on an empty key.

The same install also feeds the other direction — re-addressing a verified
principal to BCN when the backend calls it on the caller's behalf — so BCN's
trust values are pinned here too, and an unresolved key denies that as well.
"""

from __future__ import annotations

import logging
import time

import jwt
import pytest

from agentclaw.community.core.gateway_principal import (
    MIN_SIGNING_KEY_BYTES,
    PrincipalVerificationError,
    is_weak_signing_key,
    key_fingerprint,
)
from agentclaw.community.plugin_api.secret_resolver import SecretResolver
from agentclaw.community.utils.gateway_principal_config import (
    get_bcn_principal_signer_config,
    get_principal_verifier_config,
    init_principal_verifier_config,
    reset_principal_verifier_config_cache,
    resign_principal_for_bcn,
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


def test_bcn_signer_reads_the_same_installed_key():
    """One shared secret, two views of it — never a second credential."""
    init_principal_verifier_config(_FakeResolver(_Secret(KEY)), SECRET_NAME, strict=False)

    signer = get_bcn_principal_signer_config()

    assert signer.signing_key == get_principal_verifier_config().signing_key
    assert signer.key_fingerprint == key_fingerprint(KEY)


def test_bcn_trust_values_are_fixed_in_code():
    """The envelope a token addressed to BCN carries.

    ``aud=bcs`` is what makes the friend-approval callback verifiable there at
    all and ``kid=bare`` is checked before the signature is — both are BCN's
    requirements, so changing them there means changing them here.

    ``iss`` runs the other way: it names the component that signed this copy,
    which is us, so it is the name the gateway knows us by rather than the
    gateway's own. BCN has to be configured to trust it.
    """
    signer = get_bcn_principal_signer_config()

    assert signer.audience == "bcs"
    assert signer.issuer == "backend"
    assert signer.key_id == "bare"


def test_the_issuer_we_assert_is_the_name_the_gateway_knows_us_by():
    """One spelling for "this component", used in both directions.

    The gateway addresses us as ``backend`` (its ``servers:`` key, which its
    forwarder signs into ``aud``), so that is the audience we accept — and the
    identity we issue under when we sign a token of our own. Deriving both from
    one constant keeps a rename from changing only half of it.
    """
    init_principal_verifier_config(_FakeResolver(_Secret(KEY)), SECRET_NAME, strict=False)

    assert (
        get_bcn_principal_signer_config().issuer
        == get_principal_verifier_config().audience
    )


def test_re_addressing_produces_a_token_bcn_accepts():
    """The whole fix, through the installed configuration rather than around it.

    The inbound token is addressed to ``backend``, which is what BCN refuses and
    what made every friend-approval callback fail. The re-addressed copy is
    judged here exactly as BCN judges it — ``alg``/``typ``/``kid`` on the header,
    ``iss``/``aud`` and the time claims under the shared key — with the caller
    unchanged. ``iss`` is this component, since this component signed it.
    """
    init_principal_verifier_config(_FakeResolver(_Secret(KEY)), SECRET_NAME, strict=False)
    principals = [
        {
            "type": "user",
            "subject": {"id": "u-1", "username": "alice@example.com"},
        }
    ]
    now = int(time.time())
    inbound = jwt.encode(
        {
            "iss": "gateway",
            "aud": "backend",
            "iat": now,
            "exp": now + 60,
            "principals": principals,
        },
        KEY,
        algorithm="HS256",
        headers={"kid": "bare"},
    )

    readdressed = resign_principal_for_bcn(inbound)

    assert jwt.get_unverified_header(readdressed) == {
        "alg": "HS256",
        "typ": "JWT",
        "kid": "bare",
    }
    claims = jwt.decode(
        readdressed,
        KEY,
        algorithms=["HS256"],
        audience="bcs",
        issuer="backend",
        options={"require": ["exp", "iat", "iss", "aud"]},
    )
    assert claims["principals"] == principals
    assert (claims["iat"], claims["exp"]) == (now, now + 60)


def test_re_addressing_to_bcn_denies_before_boot():
    """No key, no re-addressed credential — the same fail-closed as verifying."""
    with pytest.raises(PrincipalVerificationError, match="no principal signing key"):
        resign_principal_for_bcn("any.token.value")


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
    """A committed shared secret is a committed credential.

    "No key" fails safe rather than open. The gateway's ``bare`` signer used to
    keep a dev fallback and no longer does, so both ends of this contract now
    answer an unresolvable key the same way.
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


# ── boot-time diagnostics ────────────────────────────────────────────────────
#
# The two halves of this contract never meet at request time: the gateway signs
# successfully on every request and only the backend ever sees a mismatch, as a
# signature failure it cannot attribute. Boot is therefore the one place the two
# keys can be compared, and these tests pin what makes that comparison possible.


def test_boot_logs_a_fingerprint_not_the_key(caplog):
    """The line an operator diffs against the gateway's.

    A fingerprint rather than the key itself: comparable across components,
    useless to anyone who reads the log.
    """
    with caplog.at_level(logging.INFO):
        init_principal_verifier_config(_FakeResolver(_Secret(KEY)), SECRET_NAME, strict=False)

    line = "\n".join(r.getMessage() for r in caplog.records)
    assert f"key fp={key_fingerprint(KEY)}" in line
    assert f"key len={len(KEY)}" in line, "length separates keys differing only by whitespace"
    assert SECRET_NAME in line, "names which secret was read, for the wrong-name case"
    assert KEY not in line, "the key itself never reaches a log"


def test_boot_logs_the_contract_it_will_enforce(caplog):
    """``aud``/``iss`` are hardcoded here and configurable on the gateway.

    Logging them turns "the gateway's issuer was changed and every request now
    401s" into something readable from a boot line rather than from source.
    """
    with caplog.at_level(logging.INFO):
        init_principal_verifier_config(_FakeResolver(_Secret(KEY)), SECRET_NAME, strict=False)

    line = "\n".join(r.getMessage() for r in caplog.records)
    assert "aud='backend'" in line
    assert "iss='gateway'" in line


def test_surrounding_whitespace_is_reported_with_both_fingerprints(caplog):
    """Stripping is otherwise silent, and silence is what makes this bite.

    A gateway released before it stripped signs with the untrimmed bytes, so
    naming *both* fingerprints makes a mixed-version rollout a grep rather than
    a second incident.
    """
    with caplog.at_level(logging.WARNING):
        init_principal_verifier_config(
            _FakeResolver(_Secret(f"  {KEY}\n")), SECRET_NAME, strict=False
        )

    line = "\n".join(r.getMessage() for r in caplog.records)
    assert "whitespace" in line
    assert f"untrimmed={key_fingerprint(f'  {KEY}' + chr(10))}" in line
    assert f"trimmed={key_fingerprint(KEY)}" in line
    assert get_principal_verifier_config().signing_key == KEY, "still uses the trimmed key"


def test_a_clean_key_reports_no_whitespace(caplog):
    with caplog.at_level(logging.WARNING):
        init_principal_verifier_config(_FakeResolver(_Secret(KEY)), SECRET_NAME, strict=False)

    assert "whitespace" not in "\n".join(r.getMessage() for r in caplog.records)


def test_two_deployments_holding_the_same_key_log_the_same_fingerprint():
    """The property the whole diagnostic rests on."""
    assert key_fingerprint(KEY) == key_fingerprint(KEY)
    assert key_fingerprint(KEY) != key_fingerprint(KEY + "-rotated")


# ── weak keys ────────────────────────────────────────────────────────────────
#
# The boot line publishes a fingerprint, and that is only safe while the key is
# strong: against a guessable one a truncated digest confirms a dictionary guess
# offline, and confirming the shared secret means forging any caller identity.
# Nothing enforces the strength, so it is warned about rather than assumed.


WEAK_KEY = "hunter2"


def test_a_weak_key_warns_but_still_reports_its_fingerprint(caplog):
    """Withholding the fingerprint would remove the diagnostic exactly when a
    deployment is most misconfigured. The remedy is a better secret."""
    with caplog.at_level(logging.INFO):
        init_principal_verifier_config(
            _FakeResolver(_Secret(WEAK_KEY)), SECRET_NAME, strict=False
        )

    line = "\n".join(r.getMessage() for r in caplog.records)
    assert f"key fp={key_fingerprint(WEAK_KEY)}" in line, "still diagnosable"
    assert "below the 32-byte minimum" in line
    assert "forge any caller identity" in line, "says why it matters"
    assert WEAK_KEY not in line, "and never prints the key itself"


def test_a_strong_key_does_not_warn(caplog):
    with caplog.at_level(logging.WARNING):
        init_principal_verifier_config(
            _FakeResolver(_Secret(KEY)), SECRET_NAME, strict=False
        )

    assert "minimum" not in "\n".join(r.getMessage() for r in caplog.records)


def test_a_weak_key_still_configures_the_verifier(caplog):
    """A warning, not a refusal: rejecting a short key here would deny every
    request on a deployment that was at least partly working, which is a
    bigger change than this diagnostic is entitled to make."""
    init_principal_verifier_config(
        _FakeResolver(_Secret(WEAK_KEY)), SECRET_NAME, strict=False
    )

    assert get_principal_verifier_config().signing_key == WEAK_KEY


def test_the_strength_threshold_is_shared_with_the_gateway():
    """Both ends judge one shared secret by one rule."""
    assert MIN_SIGNING_KEY_BYTES == 32
    assert is_weak_signing_key("a" * 31)
    assert not is_weak_signing_key("a" * 32)
    assert not is_weak_signing_key(""), "absent is its own state, warned elsewhere"
