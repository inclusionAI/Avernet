"""Unit tests for the relay WebSocket auth seam."""

from __future__ import annotations

import base64
import json
import time

from sandboxproxy.community.core.authn import (
    authenticate_relay,
    extract_bearer_token,
    extract_token,
    extract_user_id,
    parse_target,
    verify_token,
)

_SECRET = "relay-secret"


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _sign(secret: str, payload: dict) -> str:
    header_b64 = _b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    payload_b64 = _b64(json.dumps(payload).encode())
    import hashlib
    import hmac

    sig = hmac.new(
        secret.encode(), f"{header_b64}.{payload_b64}".encode(), hashlib.sha256
    ).digest()
    return f"{header_b64}.{payload_b64}.{_b64(sig)}"


def _target(session_id: str) -> str:
    return f"LOCAL_dev1@42:20003:{session_id}"


class TestParseTarget:
    def test_valid(self) -> None:
        assert parse_target("LOCAL_dev1@42:20003:sess1") == (
            "dev1",
            "42",
            "20003",
            "sess1",
        )

    def test_non_local(self) -> None:
        assert parse_target("ARCA_1") is None

    def test_too_few_parts(self) -> None:
        assert parse_target("LOCAL_dev1@42:20003") is None


class TestExtractToken:
    def test_header(self) -> None:
        assert extract_token({"X-PROXYPASS-TOKEN": "tok"}, "/wsrelay/x") == "tok"

    def test_query(self) -> None:
        assert extract_token({}, "/wsrelay/x?x-proxypass-token=tok2") == "tok2"

    def test_none(self) -> None:
        assert extract_token({}, "/wsrelay/x") is None


class TestExtractBearer:
    def test_bearer(self) -> None:
        assert extract_bearer_token({"Authorization": "Bearer abc"}) == "abc"

    def test_non_bearer(self) -> None:
        assert extract_bearer_token({"Authorization": "Basic abc"}) is None

    def test_missing(self) -> None:
        assert extract_bearer_token({}) is None


class TestExtractUser:
    def test_sno(self) -> None:
        token = _sign(_SECRET, {"sno": "u7"})
        assert extract_user_id(token) == "u7"

    def test_no_sno(self) -> None:
        token = _sign(_SECRET, {"sub": "other"})
        assert extract_user_id(token) is None

    def test_malformed(self) -> None:
        assert extract_user_id("not-a-jwt") is None


class TestVerifyToken:
    def test_valid(self) -> None:
        token = _sign(_SECRET, {"target": _target("s1"), "exp": time.time() + 3600})
        assert verify_token(token, _SECRET) is not None

    def test_bad_signature(self) -> None:
        token = _sign("other", {"target": _target("s1")})
        assert verify_token(token, _SECRET) is None

    def test_expired(self) -> None:
        token = _sign(_SECRET, {"target": _target("s1"), "exp": time.time() - 1})
        assert verify_token(token, _SECRET) is None

    def test_empty_secret(self) -> None:
        token = _sign(_SECRET, {"target": _target("s1")})
        assert verify_token(token, "") is None

    def test_malformed_parts(self) -> None:
        assert verify_token("a.b", _SECRET) is None

    def test_non_hs256_alg(self) -> None:
        header_b64 = _b64(json.dumps({"alg": "RS256"}).encode())
        payload_b64 = _b64(json.dumps({"target": _target("s1")}).encode())
        assert verify_token(f"{header_b64}.{payload_b64}.sig", _SECRET) is None

    def test_bad_header_b64(self) -> None:
        assert verify_token(f"!!!.{_b64(b'{}')}.sig", _SECRET) is None

    def test_bad_signature_b64(self) -> None:
        header_b64 = _b64(json.dumps({"alg": "HS256"}).encode())
        payload_b64 = _b64(json.dumps({"target": _target("s1")}).encode())
        assert verify_token(f"{header_b64}.{payload_b64}.***", _SECRET) is None

    def test_bad_payload_b64(self) -> None:
        header_b64 = _b64(json.dumps({"alg": "HS256"}).encode())
        sig = _b64(b"x")
        assert verify_token(f"{header_b64}.!!!.{sig}", _SECRET) is None

    def test_non_dict_payload(self) -> None:
        import hashlib
        import hmac

        header_b64 = _b64(json.dumps({"alg": "HS256"}).encode())
        payload_b64 = _b64(b"[1,2,3]")
        si = f"{header_b64}.{payload_b64}".encode()
        sig = _b64(hmac.new(_SECRET.encode(), si, hashlib.sha256).digest())
        assert verify_token(f"{header_b64}.{payload_b64}.{sig}", _SECRET) is None


class TestAuthenticateRelay:
    def test_full_auth_ok(self) -> None:
        token = _sign(_SECRET, {"target": _target("s1"), "exp": time.time() + 3600})
        result = authenticate_relay(
            {"X-PROXYPASS-TOKEN": token}, "/wsrelay/s1", "wsrelay", _SECRET
        )
        assert result.ok is True
        assert result.session_id == "s1"

    def test_full_auth_session_mismatch(self) -> None:
        token = _sign(_SECRET, {"target": _target("OTHER"), "exp": time.time() + 3600})
        result = authenticate_relay(
            {"X-PROXYPASS-TOKEN": token}, "/wsrelay/s1", "wsrelay", _SECRET
        )
        assert result.ok is False
        assert result.reason == "session mismatch"

    def test_full_auth_missing_token(self) -> None:
        result = authenticate_relay({}, "/wsrelay/s1", "wsrelay", _SECRET)
        assert result.ok is False
        assert result.status_code == 401

    def test_light_auth_skips_signature(self) -> None:
        # Wrong secret still passes light auth (no signature check)
        token = _sign("wrong", {"target": _target("s1"), "exp": time.time() - 100})
        result = authenticate_relay(
            {"X-PROXYPASS-TOKEN": token}, "/wsrevrelay/s1", "wsrevrelay", _SECRET
        )
        assert result.ok is True

    def test_light_auth_missing_target(self) -> None:
        token = _sign(_SECRET, {"sub": "u1"})
        result = authenticate_relay(
            {"X-PROXYPASS-TOKEN": token}, "/wsrevrelay/s1", "wsrevrelay", _SECRET
        )
        assert result.ok is False
        assert result.reason == "missing target in token"

    def test_bearer_fallback_with_sno(self) -> None:
        token = _sign(_SECRET, {"sno": "u99", "target": _target("s1")})
        result = authenticate_relay(
            {"Authorization": f"Bearer {token}"},
            "/wsrevrelay/s1",
            "wsrevrelay",
            _SECRET,
        )
        assert result.ok is True
        assert result.user_id == "u99"

    def test_full_auth_invalid_token(self) -> None:
        result = authenticate_relay(
            {"X-PROXYPASS-TOKEN": "garbage"},
            "/wsrelay/s1",
            "wsrelay",
            _SECRET,
        )
        assert result.ok is False
        assert result.reason == "invalid token format"

    def test_light_auth_invalid_target(self) -> None:
        token = _sign(_SECRET, {"target": "ARCA_123"})
        result = authenticate_relay(
            {"X-PROXYPASS-TOKEN": token},
            "/wsrevrelay/s1",
            "wsrevrelay",
            _SECRET,
        )
        assert result.ok is False
        assert result.reason == "invalid target format"

    def test_missing_target_full(self) -> None:
        token = _sign(_SECRET, {"sub": "u1", "exp": time.time() + 3600})
        result = authenticate_relay(
            {"X-PROXYPASS-TOKEN": token}, "/wsrelay/s1", "wsrelay", _SECRET
        )
        assert result.ok is False
        assert result.reason == "missing target in token"

    def test_bearer_missing_sno(self) -> None:
        token = _sign(_SECRET, {"sub": "u1", "target": _target("s1")})
        result = authenticate_relay(
            {"Authorization": f"Bearer {token}"},
            "/wsrevrelay/s1",
            "wsrevrelay",
            _SECRET,
        )
        assert result.ok is False
        assert result.reason == "missing or invalid sno"
