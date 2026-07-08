"""Mock crypto plugin — static-key, deterministic implementation for testing."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from base64 import urlsafe_b64decode, urlsafe_b64encode
from typing import Any

from secbaas.spi.crypto import CryptoPlugin

_STUB_SM4_KEY = b"\x01" * 16


class StubCryptoPlugin(CryptoPlugin):
    """Mock implementation of CryptoPlugin for testing.

    SM4 is simulated via AES-128-CBC (same block size). AES-GCM and
    HMAC-SHA256 JWT use the real algorithms with cryptography.
    """

    def __init__(self, sm4_key: bytes | None = None) -> None:
        self._sm4_key = sm4_key or _STUB_SM4_KEY

    def sm4_encrypt(self, plaintext: str) -> str:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

        iv = os.urandom(16)
        padded = _pkcs7_pad(plaintext.encode())
        cipher = Cipher(algorithms.AES(self._sm4_key), modes.CBC(iv))
        encryptor = cipher.encryptor()
        ct = encryptor.update(padded) + encryptor.finalize()
        return (iv + ct).hex()

    def sm4_decrypt(self, ciphertext_b64: str) -> str:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

        data = bytes.fromhex(ciphertext_b64)
        if len(data) < 16:
            raise ValueError("Invalid ciphertext: too short")
        iv, ct = data[:16], data[16:]
        cipher = Cipher(algorithms.AES(self._sm4_key), modes.CBC(iv))
        decryptor = cipher.decryptor()
        padded = decryptor.update(ct) + decryptor.finalize()
        return _pkcs7_unpad(padded).decode()

    def symmetric_encrypt(self, plaintext: str, secret_key: str) -> str:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        key = hashlib.sha256(secret_key.encode()).digest()
        aesgcm = AESGCM(key)
        nonce = os.urandom(12)
        ct = aesgcm.encrypt(nonce, plaintext.encode(), None)
        return _b64url(nonce + ct)

    def symmetric_decrypt(self, ciphertext_b64: str, secret_key: str) -> str:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        key = hashlib.sha256(secret_key.encode()).digest()
        aesgcm = AESGCM(key)
        data = _b64url_decode(ciphertext_b64)
        if len(data) < 12:
            raise ValueError("Invalid ciphertext: too short")
        nonce, ct = data[:12], data[12:]
        return aesgcm.decrypt(nonce, ct, None).decode()

    def generate_jwt(self, target: str, secret_key: str, ttl_seconds: int = 300) -> str:
        header_b64 = _b64url(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
        payload = json.dumps({"target": target, "exp": int(time.time()) + ttl_seconds})
        payload_b64 = _b64url(payload.encode())
        signing_input = f"{header_b64}.{payload_b64}"
        sig = _b64url(
            hmac.new(
                secret_key.encode(), signing_input.encode(), hashlib.sha256
            ).digest()
        )
        return f"{header_b64}.{payload_b64}.{sig}"

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


def _b64url(data: bytes) -> str:
    return urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64url_decode(data: str) -> bytes:
    padding = 4 - len(data) % 4
    if padding != 4:
        data += "=" * padding
    return urlsafe_b64decode(data)


def _pkcs7_pad(data: bytes, block_size: int = 16) -> bytes:
    pad_len = block_size - (len(data) % block_size)
    return data + bytes([pad_len] * pad_len)


def _pkcs7_unpad(data: bytes, block_size: int = 16) -> bytes:
    if not data:
        return data
    pad_len = data[-1]
    if pad_len < 1 or pad_len > block_size:
        return data
    return data[:-pad_len]
