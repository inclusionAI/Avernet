from __future__ import annotations

from typing import Protocol


class CryptoPlugin(Protocol):
    """Plugin protocol for cryptographic operations.

    Covers symmetric encryption (SM4, AES-GCM) and JWT token generation.

    Implementations:
    - RealCryptoPlugin: wraps gmssl.sm4 + cryptography.AESGCM for production.
    - StubCryptoPlugin: no-op / static keys for tests.
    """

    def sm4_encrypt(self, plaintext: str) -> str:
        """SM4-CBC encrypt with PKCS7 padding.

        Uses the platform-wide SM4 key (provisioned via MIST in production).

        Args:
            plaintext: Text to encrypt.

        Returns:
            Base64-encoded ciphertext.
        """
        ...

    def sm4_decrypt(self, ciphertext_b64: str) -> str:
        """SM4-CBC decrypt with PKCS7 unpadding.

        Args:
            ciphertext_b64: Base64-encoded ciphertext.

        Returns:
            Decrypted plaintext.
        """
        ...

    def symmetric_encrypt(self, plaintext: str, secret_key: str) -> str:
        """AES-GCM encrypt with a given key.

        Args:
            plaintext: Text to encrypt.
            secret_key: Symmetric encryption key.

        Returns:
            Base64-encoded ciphertext (includes nonce).
        """
        ...

    def symmetric_decrypt(self, ciphertext_b64: str, secret_key: str) -> str:
        """AES-GCM decrypt with a given key.

        Args:
            ciphertext_b64: Base64-encoded ciphertext (includes nonce).
            secret_key: Symmetric encryption key.

        Returns:
            Decrypted plaintext.
        """
        ...

    def generate_jwt(self, target: str, secret_key: str, ttl_seconds: int = 120) -> str:
        """Generate an HS256 JWT token.

        Args:
            target: Token target (payload).
            secret_key: HMAC signing key.
            ttl_seconds: Token TTL in seconds.

        Returns:
            JWT token string.
        """
        ...

    def verify_jwt(
        self, token: str, secret_key: str
    ) -> tuple[bool, str | None, dict | None]:
        """Verify an HS256 JWT token.

        Args:
            token: JWT token string.
            secret_key: HMAC signing key.

        Returns:
            Tuple of (is_valid, error_message, payload_dict).
        """
        ...
