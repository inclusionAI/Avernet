from secbaas.spi.secret import DEV_SM4_KEY

"""Unit tests for secret_utils module (pure crypto functions only)."""

import base64
import json
import time
from unittest.mock import patch

import pytest

from secbaas.core.utils import secret_utils


class TestGenerateJwtToken:
    """Tests for generate_jwt_token function."""

    def test_generate_jwt_token_creates_valid_structure(self):
        token = secret_utils.generate_jwt_token("test-target", "secret-key")
        parts = token.split(".")
        assert len(parts) == 3

    def test_generate_jwt_token_contains_target_in_payload(self):
        target = "my-target"
        token = secret_utils.generate_jwt_token(target, "secret-key")
        parts = token.split(".")
        payload_b64 = parts[1]
        padding = 4 - len(payload_b64) % 4
        if padding != 4:
            payload_b64 += "=" * padding
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
        assert payload["target"] == target

    def test_generate_jwt_token_contains_exp(self):
        ttl = 120
        token = secret_utils.generate_jwt_token("test-target", "secret-key", ttl=ttl)
        parts = token.split(".")
        payload_b64 = parts[1]
        padding = 4 - len(payload_b64) % 4
        if padding != 4:
            payload_b64 += "=" * padding
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
        assert "exp" in payload
        assert payload["exp"] > int(time.time())


class TestVerifyJwtToken:
    """Tests for verify_jwt_token function."""

    def test_verify_jwt_token_validates_correct_token(self):
        target = "test-target"
        secret_key = "my-secret-key"
        token = secret_utils.generate_jwt_token(target, secret_key, ttl=3600)
        is_valid, error, payload = secret_utils.verify_jwt_token(token, secret_key)
        assert is_valid is True
        assert error is None
        assert payload is not None
        assert payload["target"] == target

    def test_verify_jwt_token_rejects_invalid_signature(self):
        token = secret_utils.generate_jwt_token("test-target", "correct-key")
        is_valid, error, payload = secret_utils.verify_jwt_token(token, "wrong-key")
        assert is_valid is False
        assert error == "Invalid signature"
        assert payload is None

    def test_verify_jwt_token_rejects_expired_token(self):
        target = "test-target"
        secret_key = "my-secret-key"
        with patch("time.time", return_value=1000000000):
            token = secret_utils.generate_jwt_token(target, secret_key, ttl=60)
        with patch("time.time", return_value=1000001000):
            is_valid, error, payload = secret_utils.verify_jwt_token(token, secret_key)
        assert is_valid is False
        assert error == "Token expired"
        assert payload is not None
        assert payload["target"] == target

    def test_verify_jwt_token_rejects_malformed_token(self):
        invalid_token = "header.payload"
        is_valid, error, payload = secret_utils.verify_jwt_token(
            invalid_token, "secret"
        )
        assert is_valid is False
        assert error == "Invalid token format"
        assert payload is None

    def test_verify_jwt_token_rejects_token_with_more_parts(self):
        invalid_token = "a.b.c.d"
        is_valid, error, payload = secret_utils.verify_jwt_token(
            invalid_token, "secret"
        )
        assert is_valid is False
        assert error == "Invalid token format"
        assert payload is None


class TestSymmetricEncrypt:
    """Tests for symmetric_encrypt and symmetric_decrypt functions."""

    def test_symmetric_encrypt_decrypt_roundtrip_succeeds(self):
        plaintext = "Hello, World!"
        secret_key = "my-encryption-key"
        ciphertext = secret_utils.symmetric_encrypt(plaintext, secret_key)
        decrypted = secret_utils.symmetric_decrypt(ciphertext, secret_key)
        assert decrypted == plaintext

    def test_symmetric_encrypt_produces_different_ciphertexts(self):
        plaintext = "Same message"
        secret_key = "same-key"
        ciphertext1 = secret_utils.symmetric_encrypt(plaintext, secret_key)
        ciphertext2 = secret_utils.symmetric_encrypt(plaintext, secret_key)
        assert ciphertext1 != ciphertext2

    def test_symmetric_decrypt_fails_with_wrong_key(self):
        plaintext = "Secret message"
        ciphertext = secret_utils.symmetric_encrypt(plaintext, "correct-key")
        with pytest.raises(ValueError, match="解密失败"):
            secret_utils.symmetric_decrypt(ciphertext, "wrong-key")

    def test_symmetric_encrypt_handles_empty_string(self):
        plaintext = ""
        secret_key = "test-key"
        ciphertext = secret_utils.symmetric_encrypt(plaintext, secret_key)
        decrypted = secret_utils.symmetric_decrypt(ciphertext, secret_key)
        assert decrypted == ""

    def test_symmetric_encrypt_handles_unicode(self):
        plaintext = "你好世界 🌍"
        secret_key = "unicode-key"
        ciphertext = secret_utils.symmetric_encrypt(plaintext, secret_key)
        decrypted = secret_utils.symmetric_decrypt(ciphertext, secret_key)
        assert decrypted == plaintext

    def test_symmetric_decrypt_fails_with_invalid_ciphertext(self):
        with pytest.raises(ValueError):
            secret_utils.symmetric_decrypt("not-valid-base64!", "any-key")


_STUB_SM4_KEY = DEV_SM4_KEY


class TestCommonSm4EncryptDecrypt:
    """Tests for common_sm4_encrypt and common_sm4_decrypt (with explicit key)."""

    def test_common_sm4_encrypt_decrypt_roundtrip_succeeds(self):
        plaintext = "Hello, World!"
        ciphertext = secret_utils.common_sm4_encrypt(plaintext, _STUB_SM4_KEY)
        decrypted = secret_utils.common_sm4_decrypt(ciphertext, _STUB_SM4_KEY)
        assert decrypted == plaintext

    def test_common_sm4_encrypt_produces_different_ciphertexts(self):
        plaintext = "Same message"
        ciphertext1 = secret_utils.common_sm4_encrypt(plaintext, _STUB_SM4_KEY)
        ciphertext2 = secret_utils.common_sm4_encrypt(plaintext, _STUB_SM4_KEY)
        assert ciphertext1 != ciphertext2

    def test_common_sm4_decrypt_fails_with_invalid_ciphertext(self):
        with pytest.raises(ValueError, match="SM4 解密失败"):
            secret_utils.common_sm4_decrypt("not-valid-base64!", _STUB_SM4_KEY)

    def test_common_sm4_encrypt_handles_empty_string(self):
        plaintext = ""
        ciphertext = secret_utils.common_sm4_encrypt(plaintext, _STUB_SM4_KEY)
        decrypted = secret_utils.common_sm4_decrypt(ciphertext, _STUB_SM4_KEY)
        assert decrypted == ""

    def test_common_sm4_encrypt_decrypt_handles_key_param(self):
        """SM4 works correctly with non-default key."""
        custom_key = "/oPhwiZaFMJrfiWBdTey2A=="
        plaintext = "Key param test"
        ciphertext = secret_utils.common_sm4_encrypt(plaintext, custom_key)
        decrypted = secret_utils.common_sm4_decrypt(ciphertext, custom_key)
        assert decrypted == plaintext

    def test_common_sm4_decrypt_fails_with_too_short_ciphertext(self):
        with pytest.raises(ValueError, match="SM4 解密失败"):
            secret_utils.common_sm4_decrypt("YWJjZA==", _STUB_SM4_KEY)
