from __future__ import annotations

from typing import Protocol


class SecretStorePlugin(Protocol):
    """Plugin protocol for secret storage and retrieval (MIST platform).

    Implementations:
    - RealSecretStorePlugin: wraps MIST/Layotto manager for production.
    - StubSecretStorePlugin: dict-based in-memory store for tests.
    """

    def get_secret(self, secret_name: str) -> str:
        """Retrieve a secret value from the secret store.

        Args:
            secret_name: Name of the secret to retrieve.

        Returns:
            The secret value as a string.

        Raises:
            RuntimeError: If secret is not found.
        """
        ...

    def get_kv_secret(self, secret_name: str) -> tuple[str, str]:
        """Retrieve a key-value secret pair.

        Args:
            secret_name: Name of the KV secret.

        Returns:
            A tuple of (key, value).

        Raises:
            RuntimeError: If secret is not found or malformed.
        """
        ...

    def resolve_secret(self, raw_value: str) -> str:
        """Resolve a potentially secret-referenced value.

        If raw_value starts with '@' (e.g., '@my.secret_name'),
        it is treated as a MIST secret reference and resolved.
        Otherwise, raw_value is returned as-is.

        Args:
            raw_value: Config value that may contain a '@' secret reference.

        Returns:
            The resolved secret value or the original raw_value.
        """
        ...

    def generate_proxy_token(self, target: str, ttl_seconds: int | None = None) -> str:
        """Generate a proxy authentication JWT token.

        Produces a JWT token used for nginx proxy authentication,
        signed with the MIST-provisioned proxy secret.

        Args:
            target: Target identifier (e.g., sandbox ID).
            ttl_seconds: Token TTL in seconds.

        Returns:
            JWT token string for proxy auth.
        """
        ...

    def resolve_common_sm4_key(self) -> str:
        """Resolve the common SM4 encryption key.

        Returns a base64-encoded SM4 key. In production-like environments
        this comes from MIST; in dev/test it returns a fixed key.

        Returns:
            Base64-encoded SM4 key string.
        """
        ...
