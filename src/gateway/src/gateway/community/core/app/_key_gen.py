"""The app credential scheme — a copy of secbaas's ``APIKeyGenerator``.

Mirrors ``src/baas/src/secbaas/community/core/service/api_gateway/_key_gen.py``.
secbaas's existing API-key records are migrated into ``avernet_application`` and
must keep verifying with their original plaintext keys, so the *algorithm* here
must not drift from that file: the stored hash records only its salt, leaving the
digest, the iteration count, and the encoding as implicit constants shared by
both sides. Change the scheme in both files or in neither — a one-sided change
silently invalidates every migrated key.

The executable statements are byte-identical to the upstream file, and
``tests/unit/plugins/test_app_key_gen.py`` pins that: it compares the two modules
as syntax trees with docstrings stripped, so comments and prose may differ (these
are in English per the gateway's convention; upstream's are in Chinese) while any
change to what the code *does* fails the check.
"""

import base64
import hashlib
import hmac
import re
import secrets


class APIKeyGenerator:
    """
    Key format:
      API Key : {32 random base62 characters}

    Example (upstream's, kept as written — it is illustrative only and is
    shorter than the 32 characters `generate` actually emits):
      xK9mP2nQ8rL4vT6wY1zA3bC
    """

    BASE62 = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"

    @classmethod
    def generate(cls) -> str:
        """
        Generate a single API Key.
        Returns the api_key string.
        """
        # generate api_key (32 base62 characters)
        return f"{cls._random_base62(32)}"

    @classmethod
    def hash_key(cls, api_key: str) -> str:
        """
        Hash for storage: PBKDF2 + salt.
        Safer than a plain sha256 — resists brute-force cracking.
        """
        salt = secrets.token_bytes(32)
        dk = hashlib.pbkdf2_hmac(
            hash_name="sha256", password=api_key.encode(), salt=salt, iterations=100_000
        )
        # format: base64(salt):base64(dk)
        return base64.b64encode(salt).decode() + ":" + base64.b64encode(dk).decode()

    @classmethod
    def verify_key(cls, api_key: str, stored_hash: str) -> bool:
        """Verify an API Key (constant-time comparison, resists timing attacks)."""
        try:
            salt_b64, dk_b64 = stored_hash.split(":")
            salt = base64.b64decode(salt_b64)
            dk = base64.b64decode(dk_b64)

            new_dk = hashlib.pbkdf2_hmac(
                hash_name="sha256",
                password=api_key.encode(),
                salt=salt,
                iterations=100_000,
            )
            # constant-time comparison, to prevent timing attacks
            return hmac.compare_digest(dk, new_dk)
        except Exception:
            return False

    @classmethod
    def _random_base62(cls, length: int) -> str:
        """Generate a random base62 string of the given length."""
        return "".join(secrets.choice(cls.BASE62) for _ in range(length))

    @staticmethod
    def validate_format(api_key: str) -> bool:
        """Check that a Key's format is valid."""
        pattern = r"^[0-9A-Za-z]{32}$"
        return bool(re.match(pattern, api_key))
