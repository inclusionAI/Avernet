from typing import Protocol, Any, Optional


class CacheProvider(Protocol):
    """Public cache provider contract.

    Implementations may be OSS defaults (Redis, Memcached, in-memory) or internal plugins.
    Public code must depend on this contract, not internal cache SDKs.
    """

    def get(self, key: str) -> Optional[Any]:
        """Get value from cache.

        Args:
            key: Cache key

        Returns:
            Cached value if found, None otherwise.
        """
        ...

    def set(self, key: str, value: Any, ttl: int = None) -> bool:
        """Set value in cache.

        Args:
            key: Cache key
            value: Value to cache
            ttl: Time-to-live in seconds (None for no expiration)

        Returns:
            True if successful, False otherwise.
        """
        ...

    def delete(self, key: str) -> bool:
        """Delete value from cache.

        Args:
            key: Cache key

        Returns:
            True if successful, False otherwise.
        """
        ...

    def exists(self, key: str) -> bool:
        """Check if key exists in cache.

        Args:
            key: Cache key

        Returns:
            True if key exists, False otherwise.
        """
        ...

    def clear(self) -> bool:
        """Clear all cached values.

        Returns:
            True if successful, False otherwise.
        """
        ...