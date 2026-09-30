"""Tests for agentclaw.community.utils.secret_utils."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time

import pytest

from agentclaw.community.utils import secret_utils


def test_generate_jwt_and_verify_roundtrip():
    token = secret_utils.generate_jwt_token("target-123", "super-secret", ttl=60)
    ok, err, payload = secret_utils.verify_jwt_token(token, "super-secret")
    assert ok is True
    assert err is None
    assert payload["target"] == "target-123"
    assert payload["exp"] > int(time.time())


def test_generate_jwt_has_three_parts():
    token = secret_utils.generate_jwt_token("t", "k")
    assert token.count(".") == 2


def test_verify_jwt_invalid_format():
    ok, err, payload = secret_utils.verify_jwt_token("not.a.jwt.token", "k")
    assert ok is False
    assert err == "Invalid token format"
    assert payload is None


def test_verify_jwt_wrong_signature():
    token = secret_utils.generate_jwt_token("t", "key-a")
    ok, err, _ = secret_utils.verify_jwt_token(token, "key-b")
    assert ok is False
    assert "Invalid signature" in err


def test_verify_jwt_expired():
    token = secret_utils.generate_jwt_token("t", "k", ttl=-10)
    ok, err, payload = secret_utils.verify_jwt_token(token, "k")
    assert ok is False
    assert err == "Token expired"
    assert payload is not None


def test_verify_jwt_malformed_payload():
    # Forge a token with valid-looking signature but bad payload
    header = base64.urlsafe_b64encode(
        json.dumps({"alg": "HS256", "typ": "JWT"}).encode()
    ).rstrip(b"=").decode()
    payload = "not-base64-json"
    signing = f"{header}.{payload}"
    sig = hmac.new(b"k", signing.encode(), hashlib.sha256).digest()
    sig_b64 = base64.urlsafe_b64encode(sig).rstrip(b"=").decode()
    token = f"{header}.{payload}.{sig_b64}"
    ok, err, _ = secret_utils.verify_jwt_token(token, "k")
    assert ok is False
    assert "Token verification failed" in err


def test_symmetric_encrypt_decrypt_roundtrip():
    plaintext = "hello world 中文"
    ct = secret_utils.symmetric_encrypt(plaintext, "my-key")
    assert ct != plaintext
    out = secret_utils.symmetric_decrypt(ct, "my-key")
    assert out == plaintext


def test_symmetric_decrypt_wrong_key():
    ct = secret_utils.symmetric_encrypt("secret", "key-a")
    with pytest.raises(ValueError):
        secret_utils.symmetric_decrypt(ct, "key-b")


def test_symmetric_nonce_changes_each_time():
    """Same plaintext encrypted twice must produce different ciphertext."""
    a = secret_utils.symmetric_encrypt("x", "k")
    b = secret_utils.symmetric_encrypt("x", "k")
    assert a != b


def test_symmetric_decrypt_handles_padding():
    """Base64 padding stripped/restored across varied plaintexts."""
    for text in ["a", "ab", "abc", "abcd", "abcde"]:
        ct = secret_utils.symmetric_encrypt(text, "key")
        assert secret_utils.symmetric_decrypt(ct, "key") == text
