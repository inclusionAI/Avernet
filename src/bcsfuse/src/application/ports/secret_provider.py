from typing import Protocol, Optional


class SecretProvider(Protocol):
    """Public secret provider contract.

    Implementations may be OSS defaults (environment variables) or internal plugins (MIST).
    Public code must depend on this contract, not internal secret SDKs.
    """

    def get_secret(self, key: str, default: Optional[str] = None) -> Optional[str]:
        """Get secret value by key.

        Args:
            key: Secret key
            default: Default value if key not found

        Returns:
            Secret value or default.

        Note:
            Implementations MUST NOT log or print secret values.
            Secrets should be retrieved from secure sources only.
        """
        ...

    def require_secret(self, key: str) -> str:
        """Get secret value by key, raising error if not found.

        Args:
            key: Secret key

        Returns:
            Secret value.

        Raises:
            KeyError: If secret not found and no default provided.

        Note:
            Implementations MUST NOT log or print secret values.
        """
        ...