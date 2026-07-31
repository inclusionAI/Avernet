"""Unit tests for the bare (HMAC) PrincipalSigner."""

from __future__ import annotations

import time as _time
from dataclasses import dataclass

import jwt
import pytest

from gateway.community.config import PrincipalSignerPluginConfig
from gateway.community.plugins.principal_signer.bare import (
    BarePrincipalSigner,
    PrincipalSignerConfig,
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
        _block(kid="k7", issuer="gw", ttl_seconds=30), _FakeResolver("envk")
    )
    assert cfg.signing_key == "envk"
    assert cfg.kid == "k7"
    assert cfg.issuer == "gw"
    assert cfg.ttl_seconds == 30


def test_load_signer_config_dev_fallback_when_secret_absent() -> None:
    cfg = load_signer_config(_block(), _FakeResolver(None))
    assert cfg.signing_key  # dev fallback present
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
