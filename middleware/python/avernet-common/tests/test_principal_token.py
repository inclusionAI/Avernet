"""Unit tests for the shared principal-token envelope.

Each test fixes one property every component on the contract relies on: the
pinned algorithm, the required claims, the issuer/audience checks (including
the multi-value forms a service-to-service hop needs), the lifetime handling of
issue vs re-sign, and the cross-component key fingerprint.
"""

from __future__ import annotations

import time

import jwt
import pytest

from avernet_common.principal_token import (
    MIN_SIGNING_KEY_BYTES,
    PrincipalSignerConfig,
    PrincipalTokenError,
    PrincipalVerifierConfig,
    decode_principal_token,
    is_weak_signing_key,
    issue_principal_token,
    key_fingerprint,
    sign_principal_claims,
)

KEY = "shared-hmac-secret-at-least-32-bytes-long"
PRINCIPALS = [{"type": "user", "subject": {"id": "u-1", "username": "alice"}}]

# backend calling bcs as itself: issuer is the signer, audience the receiver.
BACKEND_TO_BCS = PrincipalSignerConfig(
    signing_key=KEY, audience="bcs", issuer="backend", key_id="bare"
)
BCS = PrincipalVerifierConfig(signing_key=KEY, audience="bcs", issuer="backend")


def _issue(signer: PrincipalSignerConfig = BACKEND_TO_BCS, **kwargs) -> str:
    return issue_principal_token(
        {"principals": PRINCIPALS}, signer, ttl_seconds=60, **kwargs
    )


# ── issue ────────────────────────────────────────────────────────────────────


def test_issued_token_round_trips_with_the_envelope_parameters():
    claims = decode_principal_token(_issue(), BCS)

    assert claims["iss"] == "backend"
    assert claims["aud"] == "bcs"
    assert claims["principals"] == PRINCIPALS
    assert claims["exp"] - claims["iat"] == 60


def test_issued_token_header_pins_hs256_and_the_signer_kid():
    header = jwt.get_unverified_header(_issue())

    assert header["alg"] == "HS256"
    assert header["kid"] == "bare"


def test_issue_stamps_integer_times_from_now():
    claims = jwt.decode(
        _issue(now=1_700_000_000.9),
        KEY,
        algorithms=["HS256"],
        audience="bcs",
        options={"verify_exp": False},
    )

    assert claims["iat"] == 1_700_000_000
    assert claims["exp"] == 1_700_000_060


def test_issue_overrides_caller_supplied_envelope_and_lifetime():
    token = issue_principal_token(
        {"principals": PRINCIPALS, "iss": "gateway", "aud": "x", "exp": 1, "iat": 1},
        BACKEND_TO_BCS,
        ttl_seconds=60,
    )
    claims = decode_principal_token(token, BCS)

    assert (claims["iss"], claims["aud"]) == ("backend", "bcs")
    assert claims["exp"] > time.time()


@pytest.mark.parametrize("ttl", [0, -1])
def test_issue_refuses_a_non_positive_ttl(ttl: int):
    with pytest.raises(PrincipalTokenError, match="ttl must be positive"):
        issue_principal_token({}, BACKEND_TO_BCS, ttl_seconds=ttl)


def test_issue_refuses_an_empty_signing_key():
    signer = PrincipalSignerConfig(
        signing_key="", audience="bcs", issuer="backend", key_id="bare"
    )
    with pytest.raises(PrincipalTokenError, match="no principal signing key"):
        _issue(signer)


# ── re-sign ──────────────────────────────────────────────────────────────────


def test_resign_replaces_the_envelope_and_keeps_the_lifetime():
    gateway = PrincipalSignerConfig(
        signing_key=KEY, audience="backend", issuer="gateway", key_id="bare"
    )
    original = decode_principal_token(
        _issue(gateway),
        PrincipalVerifierConfig(signing_key=KEY, audience="backend", issuer="gateway"),
    )

    resigned = sign_principal_claims(original, BACKEND_TO_BCS)
    claims = decode_principal_token(resigned, BCS)

    assert (claims["iss"], claims["aud"]) == ("backend", "bcs")
    assert (claims["iat"], claims["exp"]) == (original["iat"], original["exp"])
    assert claims["principals"] == original["principals"]


def test_resign_does_not_mutate_its_input():
    claims = {"principals": PRINCIPALS, "iss": "gateway", "aud": "backend"}
    sign_principal_claims(claims, BACKEND_TO_BCS)

    assert (claims["iss"], claims["aud"]) == ("gateway", "backend")


# ── decode ───────────────────────────────────────────────────────────────────


def test_decode_refuses_another_components_audience():
    other = PrincipalVerifierConfig(signing_key=KEY, audience="baas", issuer="backend")
    with pytest.raises(PrincipalTokenError, match="Audience doesn't match"):
        decode_principal_token(_issue(), other)


def test_decode_refuses_an_untrusted_issuer():
    gateway_only = PrincipalVerifierConfig(
        signing_key=KEY, audience="bcs", issuer="gateway"
    )
    with pytest.raises(PrincipalTokenError, match="Invalid issuer"):
        decode_principal_token(_issue(), gateway_only)


def test_decode_accepts_any_issuer_in_an_allow_list():
    verifier = PrincipalVerifierConfig(
        signing_key=KEY, audience="bcs", issuer=("gateway", "backend")
    )
    assert decode_principal_token(_issue(), verifier)["iss"] == "backend"


def test_decode_accepts_any_audience_in_a_tuple():
    verifier = PrincipalVerifierConfig(
        signing_key=KEY, audience=("bcs", "bcn"), issuer="backend"
    )
    assert decode_principal_token(_issue(), verifier)["aud"] == "bcs"


def test_decode_can_skip_the_audience_check_but_never_the_issuer():
    verifier = PrincipalVerifierConfig(
        signing_key=KEY, audience="anything", issuer="backend", verify_audience=False
    )
    assert decode_principal_token(_issue(), verifier)["aud"] == "bcs"

    wrong_issuer = PrincipalVerifierConfig(
        signing_key=KEY, audience="anything", issuer="gateway", verify_audience=False
    )
    with pytest.raises(PrincipalTokenError, match="Invalid issuer"):
        decode_principal_token(_issue(), wrong_issuer)


def test_decode_refuses_an_expired_token_beyond_the_leeway():
    token = _issue(now=time.time() - 60 - 6)
    with pytest.raises(PrincipalTokenError, match="expired"):
        decode_principal_token(token, BCS)


def test_decode_allows_a_few_seconds_of_clock_skew():
    token = _issue(now=time.time() - 60 - 2)
    assert decode_principal_token(token, BCS)["iss"] == "backend"


@pytest.mark.parametrize("missing", ["exp", "iat", "iss"])
def test_decode_requires_the_envelope_claims(missing: str):
    claims = {"iss": "backend", "aud": "bcs", "iat": int(time.time())}
    claims["exp"] = claims["iat"] + 60
    del claims[missing]
    token = jwt.encode(claims, KEY, algorithm="HS256")

    with pytest.raises(PrincipalTokenError, match=missing):
        decode_principal_token(token, BCS)


def test_decode_refuses_an_algorithm_other_than_hs256():
    token = jwt.encode(
        {"iss": "backend", "aud": "bcs", "iat": int(time.time()),
         "exp": int(time.time()) + 60},
        # 64+ bytes, the HS512 minimum; the alg check fails before the
        # signature is looked at.
        KEY * 2,
        algorithm="HS512",
    )
    with pytest.raises(PrincipalTokenError, match="alg value is not allowed"):
        decode_principal_token(token, BCS)


def test_decode_fails_closed_without_a_key_or_a_token():
    with pytest.raises(PrincipalTokenError, match="no principal signing key"):
        decode_principal_token(
            _issue(),
            PrincipalVerifierConfig(signing_key="", audience="bcs", issuer="backend"),
        )
    with pytest.raises(PrincipalTokenError, match="empty principal token"):
        decode_principal_token("", BCS)


def test_a_key_mismatch_names_our_contract_and_the_unverified_header():
    forged = PrincipalSignerConfig(
        signing_key="not-the-shared-secret-but-also-32-bytes+",
        audience="bcs",
        issuer="backend",
        key_id="bare\nINJECTED",
    )
    with pytest.raises(PrincipalTokenError) as exc:
        decode_principal_token(_issue(forged), BCS)

    message = str(exc.value)
    assert "Signature verification failed" in message
    assert f"verifier key fp={key_fingerprint(KEY)}" in message
    assert "expects aud='bcs' iss='backend'" in message
    assert "unverified caller-supplied header" in message
    # repr-escaped, so a crafted kid cannot start a new log line.
    assert "\n" not in message


def test_an_unparseable_token_is_reported_as_such():
    with pytest.raises(PrincipalTokenError, match="unparseable JOSE header"):
        decode_principal_token("not-a-jwt", BCS)


# ── key diagnostics ──────────────────────────────────────────────────────────


def test_key_fingerprint_matches_the_cross_component_golden_values():
    """Pinned by literal value; the gateway's own copy pins the same ones.

    Change these only together with ``apps/gateway``'s
    ``plugins/principal_signer/bare/_plugin.py`` and its test.
    """
    assert key_fingerprint("k" * 32) == "5e318f8c"
    assert key_fingerprint("a-shared-secret-of-at-least-32-bytes!!") == "eb128a7a"
    assert key_fingerprint("rotated-shared-secret-32-bytes-min!!!!") == "21654248"


def test_fingerprint_of_no_key_reads_as_unset():
    assert key_fingerprint("") == "unset"


def test_configs_expose_their_own_fingerprint():
    assert BACKEND_TO_BCS.key_fingerprint == key_fingerprint(KEY)
    assert BCS.key_fingerprint == key_fingerprint(KEY)


def test_weak_key_is_measured_in_bytes_and_empty_is_not_weak():
    assert MIN_SIGNING_KEY_BYTES == 32
    assert is_weak_signing_key("k" * 31)
    assert not is_weak_signing_key("k" * 32)
    # 11 characters, 33 bytes in UTF-8.
    assert not is_weak_signing_key("密" * 11)
    assert not is_weak_signing_key("")
