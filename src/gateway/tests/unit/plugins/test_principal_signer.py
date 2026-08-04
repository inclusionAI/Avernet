"""Unit tests for the bare (HMAC) PrincipalSigner."""

from __future__ import annotations

import logging
import pathlib
import time as _time
from contextlib import contextmanager
from dataclasses import dataclass

import jwt
import pytest

from gateway.community.config import PrincipalSignerPluginConfig
from gateway.community.logger import get_logger
from gateway.community.plugins.principal_signer.bare import (
    BarePrincipalSigner,
    PrincipalSignerConfig,
    PrincipalSigningKeyMissingError,
    key_fingerprint,
    load_signer_config,
)
from gateway.community.spi.authn import AppPrincipal, ThirdPartyApp
from gateway.community.spi.secret_resolver import SecretResolver

_PRINCIPAL_HEADER = "X-Avernet-Principal"  # noqa: F841  (documented contract)
# Use the real wall clock so PyJWT's ``iat``/``exp`` validation (which checks
# against the actual current time, not the injected signer clock) accepts the
# token. The signer itself still uses this fixed value via its injected clock.
_FIXED_NOW = int(_time.time())


def _cfg(key: str = "k") -> PrincipalSignerConfig:
    return PrincipalSignerConfig(signing_key=key, kid="bare", ttl_seconds=60)


def _app_principal() -> AppPrincipal:
    return AppPrincipal(
        tenant="t",
        app=ThirdPartyApp(app_id=1, app_name="Demo", owners="org-1", tenant="t"),
    )


async def test_sign_returns_decodable_jwt_with_expected_claims() -> None:
    signer = BarePrincipalSigner(_cfg(), clock=lambda: _FIXED_NOW)
    token = await signer.sign({"app": _app_principal()}, audience="secbaas")

    decoded = jwt.decode(
        token, "k", algorithms=["HS256"], audience="secbaas", issuer="gateway"
    )
    assert decoded["iss"] == "gateway"
    assert decoded["aud"] == "secbaas"
    assert decoded["iat"] == _FIXED_NOW
    assert decoded["exp"] == _FIXED_NOW + 60
    assert decoded["principals"] == [_app_principal().model_dump(mode="json")]


async def test_kid_is_carried_in_jose_header() -> None:
    signer = BarePrincipalSigner(
        PrincipalSignerConfig(signing_key="k", kid="rot-7"), clock=lambda: _FIXED_NOW
    )
    token = await signer.sign({}, audience="secbaas")
    assert jwt.get_unverified_header(token)["kid"] == "rot-7"


async def test_wrong_audience_rejected_on_decode() -> None:
    signer = BarePrincipalSigner(_cfg(), clock=lambda: _FIXED_NOW)
    token = await signer.sign({}, audience="secbaas")
    with pytest.raises(jwt.InvalidAudienceError):
        jwt.decode(
            token, "k", algorithms=["HS256"], audience="engine", issuer="gateway"
        )


async def test_wrong_key_rejected_on_decode() -> None:
    signer = BarePrincipalSigner(_cfg(), clock=lambda: _FIXED_NOW)
    token = await signer.sign({}, audience="secbaas")
    with pytest.raises(jwt.InvalidSignatureError):
        jwt.decode(
            token, "other", algorithms=["HS256"], audience="secbaas", issuer="gateway"
        )


async def test_empty_principals_still_signs() -> None:
    signer = BarePrincipalSigner(_cfg(), clock=lambda: _FIXED_NOW)
    token = await signer.sign({}, audience="secbaas")
    decoded = jwt.decode(
        token, "k", algorithms=["HS256"], audience="secbaas", issuer="gateway"
    )
    assert decoded["principals"] == []


@dataclass(frozen=True)
class _Secret:
    secret_user: str
    secret_value: str


class _FakeResolver(SecretResolver):
    def __init__(self, value: str | None) -> None:
        self._value = value

    def get_secret(self, secret_name: str) -> _Secret | None:
        if self._value is None:
            return None
        return _Secret(secret_user="", secret_value=self._value)


def _block(
    *,
    secret_name: str = "principal_signing_key",
    kid: str = "bare",
    issuer: str = "gateway",
    ttl_seconds: int = 60,
) -> PrincipalSignerPluginConfig:
    return PrincipalSignerPluginConfig(
        secret_name=secret_name, kid=kid, issuer=issuer, ttl_seconds=ttl_seconds
    )


def test_load_signer_config_reads_key_from_resolver_and_params_from_config() -> None:
    cfg = load_signer_config(
        _block(kid="k7", issuer="gw", ttl_seconds=30),
        _FakeResolver("envk"),
        strict=False,
    )
    assert cfg.signing_key == "envk"
    assert cfg.kid == "k7"
    assert cfg.issuer == "gw"
    assert cfg.ttl_seconds == 30


def test_load_signer_config_leaves_the_key_empty_when_the_secret_is_absent() -> None:
    """No stand-in key. The non-secret parameters still load normally."""
    cfg = load_signer_config(_block(), _FakeResolver(None), strict=False)

    assert cfg.signing_key == ""
    assert cfg.kid == "bare"
    assert cfg.issuer == "gateway"
    assert cfg.ttl_seconds == 60


async def test_sign_token_signs_arbitrary_claims_with_kid_header() -> None:
    signer = BarePrincipalSigner(_cfg("k"), clock=lambda: _FIXED_NOW)
    token = await signer.sign_token(
        {
            "iss": "gateway",
            "typ": "access_key",
            "sub": "ak-1",
            "tenant": "t",
            "jti": "j1",
        }
    )
    decoded = jwt.decode(token, "k", algorithms=["HS256"])
    assert decoded["typ"] == "access_key"
    assert decoded["sub"] == "ak-1"
    assert decoded["tenant"] == "t"
    assert decoded["jti"] == "j1"
    assert jwt.get_unverified_header(token)["kid"] == "bare"


# ── boot-time diagnostics ────────────────────────────────────────────────────
#
# Signing never fails, so this side observes nothing when its key and the
# upstream's disagree — only the upstream does, as a signature failure it cannot
# attribute to a cause. Boot is the one place the two keys can be compared, and
# these tests pin what makes that comparison possible.


@contextmanager
def _captured(level: int = logging.INFO):
    """Collect the signer logger's own records.

    ``caplog`` cannot see them: ``BareLoggerPlugin`` sets ``propagate = False``
    on every logger it hands out, so nothing reaches the root handler pytest
    installs. Attaching to the logger under test is both deterministic and
    independent of test order — relying on caplog here made these assertions
    pass or fail depending on which test ran first.
    """
    logger = get_logger("principal_signer")
    records: list[logging.LogRecord] = []

    class _Collect(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    handler = _Collect(level)
    previous = logger.level
    logger.addHandler(handler)
    logger.setLevel(level)
    try:
        yield records
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous)


def _lines(records: list[logging.LogRecord]) -> str:
    return "\n".join(record.getMessage() for record in records)


def test_key_fingerprint_matches_the_backend_golden_values() -> None:
    """The fingerprint is a cross-component contract, pinned by literal value.

    The backend computes the identical digest over its copy of the shared key
    (``agentclaw.community.core.gateway_principal.verifier.key_fingerprint``)
    and logs it at boot; diffing the two lines is how an operator answers "do
    both ends hold the same secret?". The two implementations live in separate
    distributions with no shared package, so nothing but this test and its twin
    in ``src/backend/tests/community/core/gateway_principal/test_verifier.py``
    stops one side from drifting and quietly making the comparison meaningless.
    Change these expected values only when both change together.
    """
    assert key_fingerprint("k" * 32) == "5e318f8c"
    assert key_fingerprint("a-shared-secret-of-at-least-32-bytes!!") == "eb128a7a"
    assert key_fingerprint("rotated-shared-secret-32-bytes-min!!!!") == "21654248"


def test_fingerprint_of_no_key_reads_as_unset() -> None:
    assert key_fingerprint("") == "unset"


def test_fingerprint_does_not_leak_the_key() -> None:
    secret = "a-shared-secret-of-at-least-32-bytes!!"
    assert secret not in key_fingerprint(secret)
    assert len(key_fingerprint(secret)) == 8


def test_config_exposes_its_own_fingerprint() -> None:
    assert _cfg("envk").key_fingerprint == key_fingerprint("envk")


def test_surrounding_whitespace_is_stripped_to_match_the_backend() -> None:
    """The asymmetry this closes was invisible from both sides.

    The backend strips its copy. A key provisioned with a trailing newline —
    routine when a secret is injected from a file — otherwise left the two ends
    hashing different bytes while each looked correctly configured, and every
    token failed its signature check with nothing anywhere saying why.
    """
    cfg = load_signer_config(_block(), _FakeResolver("  envk\n"), strict=False)

    assert cfg.signing_key == "envk"
    assert cfg.key_fingerprint == key_fingerprint("envk")


def test_a_whitespace_only_key_counts_as_no_key() -> None:
    cfg = load_signer_config(_block(), _FakeResolver("   \n"), strict=False)

    assert cfg.signing_key == ""


def test_no_key_is_visible_as_unset_in_the_boot_line() -> None:
    """``key fp=unset`` against a backend reporting a real fp is the diagnosis."""
    cfg = load_signer_config(_block(), _FakeResolver(None), strict=False)

    assert cfg.key_fingerprint == "unset"


def test_boot_logs_a_fingerprint_not_the_key() -> None:
    secret = "a-shared-secret-of-at-least-32-bytes!!"

    with _captured() as records:
        load_signer_config(
            _block(issuer="gateway"), _FakeResolver(secret), strict=False
        )

    line = _lines(records)
    assert f"key fp={key_fingerprint(secret)}" in line
    assert f"key len={len(secret)}" in line
    assert "iss='gateway'" in line, "configurable here, hardcoded on the backend"
    assert secret not in line, "the key itself never reaches a log"


def test_the_missing_key_warning_says_what_will_happen() -> None:
    """A line that only reports what was not found leaves the operator to make
    the connection to a failing request during an incident."""
    with _captured(logging.WARNING) as records:
        load_signer_config(_block(), _FakeResolver(None), strict=False)

    line = _lines(records)
    assert "cannot sign" in line
    assert "principal signing failed" in line, "names the error it will produce"
    assert _block().secret_name in line, "and the secret to provision"


def test_whitespace_is_reported_with_both_fingerprints() -> None:
    """A gateway released before it stripped signs with the untrimmed bytes, so
    naming both makes a mixed-version rollout a grep rather than an incident."""
    with _captured(logging.WARNING) as records:
        load_signer_config(_block(), _FakeResolver("  envk\n"), strict=False)

    line = _lines(records)
    assert f"untrimmed={key_fingerprint('  envk' + chr(10))}" in line
    assert f"trimmed={key_fingerprint('envk')}" in line


def test_a_clean_key_reports_no_whitespace() -> None:
    with _captured(logging.WARNING) as records:
        load_signer_config(_block(), _FakeResolver("envk"), strict=False)

    assert "whitespace" not in _lines(records)


# ── no key means no signature ────────────────────────────────────────────────
#
# The dev fallback this replaces was worse than useless in both directions: a
# committed shared secret is a committed credential, and no peer would have
# accepted those tokens anyway, because the backend ships no fallback and so
# never holds the constant this side signed with. All it bought was a gateway
# that looked healthy while every request failed one hop away, attributed to
# the wrong component.


async def test_signing_without_a_key_refuses_rather_than_signing_with_nothing() -> None:
    """PyJWT will HMAC with an empty key, and that token is forgeable by anyone.

    Refusing keeps the failure inside the component that is misconfigured. The
    forwarder already turns this into ``500 principal signing failed`` with a
    traceback, so the operator is pointed here rather than at an unattributable
    401 from an upstream.
    """
    signer = BarePrincipalSigner(_cfg(key=""), clock=lambda: _FIXED_NOW)

    with pytest.raises(PrincipalSigningKeyMissingError) as exc:
        await signer.sign({"app": _app_principal()}, audience="secbaas")

    assert "secret_name" in str(exc.value), "names the knob that fixes it"


async def test_sign_token_refuses_without_a_key_too() -> None:
    """Both entry points, so no caller can route around the check."""
    signer = BarePrincipalSigner(_cfg(key=""), clock=lambda: _FIXED_NOW)

    with pytest.raises(PrincipalSigningKeyMissingError):
        await signer.sign_token({"iss": "gateway"})


def test_strict_boot_refuses_when_no_key_resolves() -> None:
    """``pre``/``prod`` fail the rollout instead of every request.

    Mirrors the backend's ``init_principal_verifier_config(..., strict=True)``:
    one contract, one rule, told the same way on both sides.
    """
    with pytest.raises(PrincipalSigningKeyMissingError, match="refuses to boot"):
        load_signer_config(_block(), _FakeResolver(None), strict=True)


def test_strict_boot_succeeds_with_a_key() -> None:
    cfg = load_signer_config(_block(), _FakeResolver("envk"), strict=True)

    assert cfg.signing_key == "envk"


def test_non_strict_boot_survives_without_a_key() -> None:
    """Local, dev and singlebox legitimately have none and must stay bootable.

    They are not thereby working: every signature attempt refuses individually.
    """
    cfg = load_signer_config(_block(), _FakeResolver(None), strict=False)

    assert cfg.signing_key == ""


def test_no_committed_key_remains_in_the_module() -> None:
    """The credential is gone from the source, not merely gated behind a flag.

    A committed shared secret is a committed credential whichever branch
    reaches it, so this asserts on the module text rather than on behavior.
    """
    from gateway.community.plugins.principal_signer.bare import _plugin

    source = pathlib.Path(_plugin.__file__).read_text(encoding="utf-8")

    assert "NOT-FOR-PROD" not in source
    assert not hasattr(_plugin, "_DEV_FALLBACK_KEY")
