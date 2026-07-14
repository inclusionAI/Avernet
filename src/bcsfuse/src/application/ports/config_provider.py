from typing import Protocol, Any


class ConfigProvider(Protocol):
    """Public configuration provider contract.

    Implementations may be OSS defaults (env vars, files) or internal plugins (DRM).
    Public code must depend on this contract, not internal config SDKs.
    """

    def get(self, key: str, default: Any = None) -> Any:
        """Get configuration value by key.

        Args:
            key: Configuration key
            default: Default value if key not found

        Returns:
            Configuration value or default.
        """
        ...

    def get_int(self, key: str, default: int = 0) -> int:
        """Get configuration value as integer.

        Args:
            key: Configuration key
            default: Default value if key not found

        Returns:
            Configuration value as integer.
        """
        ...

    def get_float(self, key: str, default: float = 0.0) -> float:
        """Get configuration value as float.

        Args:
            key: Configuration key
            default: Default value if key not found

        Returns:
            Configuration value as float.
        """
        ...

    def get_bool(self, key: str, default: bool = False) -> bool:
        """Get configuration value as boolean.

        Args:
            key: Configuration key
            default: Default value if key not found

        Returns:
            Configuration value as boolean.
        """
        ...

    def get_list(self, key: str, default: list = None) -> list:
        """Get configuration value as list.

        Args:
            key: Configuration key
            default: Default value if key not found

        Returns:
            Configuration value as list.
        """
        ...

    def get_dict(self, key: str, default: dict = None) -> dict:
        """Get configuration value as dict.

        Args:
            key: Configuration key
            default: Default value if key not found

        Returns:
            Configuration value as dict.
        """
        ...