"""Production crypto plugin — wraps gmssl.sm4 and cryptography for SM4/AES-GCM/JWT."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from secbaas.spi.crypto import CryptoPlugin
from secbaas.spi.secret import DEV_SM4_KEY  # noqa: PLC0415 — dev-only, non-prod


class RealCryptoPlugin(CryptoPlugin):
    """Production crypto plugin using gmssl.sm4 and cryptography.

    SM4 key is resolved from the injected secret store in production,
    or falls back to a fixed dev key for non-prod environments.

    Args:
        secret_store: SecretStorePlugin for resolving SM4 key from MIST.
        env: Current environment (prod, pre, gray, dev, etc.).
    """

    _DEV_SM4_KEY_B64 = DEV_SM4_KEY

    def __init__(self, secret_store: Any, env: str = "dev") -> None:
        if secret_store is None:
            raise ValueError("secret_store is required")
        self._secret_store = secret_store
        self._env = env

    def _get_sm4_key(self) -> bytes:
        if self._env in ("prod", "pre", "gray"):
            key_b64 = self._secret_store.resolve_secret(
                "@other_manual_secbaas_common_symmetric"
            )
        else:
            key_b64 = self._DEV_SM4_KEY_B64
        return base64.b64decode(key_b64)

    def sm4_encrypt(self, plaintext: str) -> str:
        from gmssl import sm4

        key = self._get_sm4_key()
        iv = os.urandom(16)
        padded = _pkcs7_padding(plaintext.encode(), 16)
        crypt = sm4.CryptSM4()
        crypt.set_key(key, sm4.SM4_ENCRYPT)
        ciphertext = crypt.crypt_cbc(iv, padded)
        return base64.b64encode(iv + ciphertext).decode()

    def sm4_decrypt(self, ciphertext_b64: str) -> str:
        from gmssl import sm4

        key = self._get_sm4_key()
        data = base64.b64decode(ciphertext_b64)
        if len(data) < 16:
            raise ValueError("Invalid ciphertext: too short")
        iv, ct = data[:16], data[16:]
        crypt = sm4.CryptSM4()
        crypt.set_key(key, sm4.SM4_DECRYPT)
        padded = crypt.crypt_cbc(iv, ct)
        return _pkcs7_unpadding(padded, 16).decode()

    def symmetric_encrypt(self, plaintext: str, secret_key: str) -> str:
        key = hashlib.sha256(secret_key.encode()).digest()
        aesgcm = AESGCM(key)
        nonce = os.urandom(12)
        ciphertext = aesgcm.encrypt(nonce, plaintext.encode(), None)
        return _b64url_encode(nonce + ciphertext)

    def symmetric_decrypt(self, ciphertext_b64: str, secret_key: str) -> str:
        key = hashlib.sha256(secret_key.encode()).digest()
        aesgcm = AESGCM(key)
        data = _b64url_decode(ciphertext_b64)
        nonce, ct = data[:12], data[12:]
        return aesgcm.decrypt(nonce, ct, None).decode()

    def generate_jwt(self, target: str, secret_key: str, ttl_seconds: int = 300) -> str:
        header_b64 = _b64url_encode(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
        payload = json.dumps({"target": target, "exp": int(time.time()) + ttl_seconds})
        payload_b64 = _b64url_encode(payload.encode())
        signing_input = f"{header_b64}.{payload_b64}"
        signature = _b64url_encode(
            hmac.new(
                secret_key.encode(), signing_input.encode(), hashlib.sha256
            ).digest()
        )
        return f"{header_b64}.{payload_b64}.{signature}"

    def verify_jwt(
        self, token: str, secret_key: str
    ) -> tuple[bool, str | None, dict[str, Any] | None]:
        try:
            parts = token.split(".")
            if len(parts) != 3:
                return False, "Invalid token format", None
            header_b64, payload_b64, sig_b64 = parts
            signing_input = f"{header_b64}.{payload_b64}"
            expected = hmac.new(
                secret_key.encode(), signing_input.encode(), hashlib.sha256
            ).digest()
            actual = _b64url_decode(sig_b64)
            if not hmac.compare_digest(expected, actual):
                return False, "Invalid signature", None
            payload = json.loads(_b64url_decode(payload_b64).decode())
            if "exp" in payload and payload["exp"] < int(time.time()):
                return False, "Token expired", payload
            return True, None, payload
        except Exception as e:
            return False, f"Token verification failed: {e}", None

    def close(self) -> None:
        pass


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64url_decode(data: str) -> bytes:
    padding = 4 - len(data) % 4
    if padding != 4:
        data += "=" * padding
    return base64.urlsafe_b64decode(data)


def _pkcs7_padding(data: bytes, block_size: int = 16) -> bytes:
    pad_len = block_size - (len(data) % block_size)
    return data + bytes([pad_len] * pad_len)


def _pkcs7_unpadding(data: bytes, block_size: int = 16) -> bytes:
    if not data:
        return data
    pad_len = data[-1]
    if pad_len < 1 or pad_len > block_size:
        return data
    return data[:-pad_len]
