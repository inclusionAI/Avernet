"""Unit tests for the JWT verifier."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time

import pytest

from sandboxproxy.community.core.authn import JwtVerifier


def _sign(secret: str, payload: dict) -> str:
    def _b64(data: bytes) -> str:
        return base64.urlsafe_b64encode(data).rstrip(b"=").decode()

    header = {"alg": "HS256", "typ": "JWT"}
    header_b64 = _b64(json.dumps(header).encode())
    payload_b64 = _b64(json.dumps(payload).encode())
    signing_input = f"{header_b64}.{payload_b64}".encode()
    sig = hmac.new(secret.encode(), signing_input, hashlib.sha256).digest()
    return f"{header_b64}.{payload_b64}.{_b64(sig)}"


def _baas_generate_jwt_token(target: str, secret: str, ttl: int = 300) -> str:
    """Mirror ``secbaas.core.utils.secret_utils.generate_jwt_token``.

    BaaS signs proxypass tokens exactly like this, and the proxy must accept
    them unchanged: HS256 over ``header.payload`` with a ``target`` claim and an
    integer ``exp`` claim.
    """
    return _sign(secret, {"target": target, "exp": int(time.time()) + ttl})


class TestJwtVerifier:
    def test_valid_token(self) -> None:
        v = JwtVerifier.from_secret("secret")
        token = _sign("secret", {"sub": "u1", "exp": time.time() + 3600})
        payload = v.verify(token)
        assert payload is not None
        assert payload["sub"] == "u1"

    def test_invalid_signature(self) -> None:
        v = JwtVerifier.from_secret("secret")
        token = _sign("wrong-secret", {"sub": "u1"})
        assert v.verify(token) is None

    def test_expired_token(self) -> None:
        v = JwtVerifier.from_secret("secret")
        token = _sign("secret", {"sub": "u1", "exp": time.time() - 100})
        assert v.verify(token) is None

    def test_malformed_token(self) -> None:
        v = JwtVerifier.from_secret("secret")
        assert v.verify("not-a-jwt") is None

    def test_empty_secret_rejected(self) -> None:
        with pytest.raises(ValueError):
            JwtVerifier.from_secret("")


class TestBaaSJwtInterop:
    """The proxy must verify tokens as issued by BaaS ``secret_utils``."""

    def test_accepts_baas_signed_token(self) -> None:
        v = JwtVerifier.from_secret("shared-secret")
        token = _baas_generate_jwt_token(
            "ARCA_ALIYUN_ACK_DEFAULT-31ebaaf6ca00@1:20003", "shared-secret"
        )
        payload = v.verify(token)
        assert payload is not None
        assert payload["target"] == "ARCA_ALIYUN_ACK_DEFAULT-31ebaaf6ca00@1:20003"
        assert isinstance(payload["exp"], int)

    def test_rejects_baas_token_with_wrong_secret(self) -> None:
        v = JwtVerifier.from_secret("shared-secret")
        token = _baas_generate_jwt_token("ARCA_ALIYUN_ACK_DEFAULT-abc@1", "other")
        assert v.verify(token) is None

    def test_rejects_baas_token_after_expiry(self) -> None:
        v = JwtVerifier.from_secret("shared-secret")
        token = _sign(
            "shared-secret", {"target": "ARCA_ALIYUN_ACK_DEFAULT-abc@1", "exp": -1}
        )
        assert v.verify(token) is None
