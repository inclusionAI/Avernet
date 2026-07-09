"""Mock secret store plugin — in-memory dict implementation for testing.

Provides a lightweight SecretStorePlugin that stores secrets in
in-memory dicts. All methods return predictable, canned values
without any external dependencies.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time

from secbaas.spi.secret import DEV_SM4_KEY, SecretStorePlugin


class StubSecretStorePlugin(SecretStorePlugin):
    """Mock implementation of SecretStorePlugin for testing.

    Secrets are stored in two in-memory dicts:
    - ``_secrets``: plain key → value pairs for ``get_secret``
    - ``_kv_secrets``: plain key → (user, value) tuples for ``get_kv_secret``

    Defaults can be pre-populated via keyword arguments to ``__init__``.
    """

    def __init__(
        self,
        secrets: dict[str, str] | None = None,
        kv_secrets: dict[str, tuple[str, str]] | None = None,
    ) -> None:
        self._secrets: dict[str, str] = dict(secrets) if secrets else {}
        self._kv_secrets: dict[str, tuple[str, str]] = (
            dict(kv_secrets) if kv_secrets else {}
        )

    # ── SecretStorePlugin protocol ───────────────────────────────────────

    def get_secret(self, secret_name: str) -> str:
        """Return the secret value for *secret_name*, or raise RuntimeError."""
        if secret_name in self._secrets:
            return self._secrets[secret_name]
        raise RuntimeError(f"Secret not found: {secret_name}")

    def get_kv_secret(self, secret_name: str) -> tuple[str, str]:
        """Return the (key, value) pair for *secret_name*, or raise RuntimeError."""
        if secret_name in self._kv_secrets:
            return self._kv_secrets[secret_name]
        raise RuntimeError(f"KV secret not found: {secret_name}")

    def resolve_secret(self, raw_value: str) -> str:
        """Resolve a ``@secret_name`` reference, or return *raw_value* as-is.

        Follows the same ``@``-prefix convention as the production
        ``resolve_secret``: values starting with ``@`` are treated as
        secret names and looked up via ``get_secret``.
        """
        if not raw_value:
            return raw_value
        if raw_value.startswith("@"):
            return self.get_secret(raw_value[1:])
        return raw_value

    def resolve_common_sm4_key(self) -> str:
        """Return a fixed stub SM4 key for testing."""
        return DEV_SM4_KEY

    def generate_proxy_token(self, target: str, ttl_seconds: int | None = None) -> str:
        """Return a deterministic stub JWT whose signature is derived from *target*.

        The token uses a fixed secret key so that consumers can verify the
        output is well-formed without depending on a real MIST backend.
        """
        ttl = ttl_seconds if ttl_seconds is not None else 300
        secret_key = "stub-proxy-secret-key"

        header_b64 = json.dumps({"alg": "HS256", "typ": "JWT"}).encode().hex()
        payload = json.dumps({"target": target, "exp": int(time.time()) + ttl})
        payload_b64 = payload.encode().hex()
        signing_input = f"{header_b64}.{payload_b64}"
        signature = hmac.new(
            secret_key.encode(), signing_input.encode(), hashlib.sha256
        ).hexdigest()

        return f"{header_b64}.{payload_b64}.{signature}"

    # ── Test helpers ─────────────────────────────────────────────────────

    def set_secret(self, name: str, value: str) -> None:
        """Pre-populate a plain secret (convenience for test setup)."""
        self._secrets[name] = value

    def set_kv_secret(self, name: str, key: str, value: str) -> None:
        """Pre-populate a KV secret (convenience for test setup)."""
        self._kv_secrets[name] = (key, value)
