"""
In-Memory Cache Provider

Simple in-memory cache implementation for OSS deployments.
"""
import time
from typing import Any, Optional


class InMemoryCacheProvider:
    """
    In-memory cache provider with TTL support.

    Suitable for single-instance OSS deployments.
    For distributed deployments, consider Redis-based implementation.
    """

    def __init__(self, default_ttl: int = 3600):
        """Initialize cache provider.

        Args:
            default_ttl: Default time-to-live in seconds (default: 1 hour).
        """
        self._cache: dict[str, tuple[Any, Optional[float]]] = {}
        self.default_ttl = default_ttl

    def get(self, key: str) -> Optional[Any]:
        """Get value from cache.

        Args:
            key: Cache key.

        Returns:
            Cached value if exists and not expired, None otherwise.
        """
        if key not in self._cache:
            return None

        value, expiry = self._cache[key]

        # Check if expired
        if expiry is not None and time.time() > expiry:
            del self._cache[key]
            return None

        return value

    def set(self, key: str, value: Any, ttl: Optional[int] = None) -> bool:
        """Set value in cache.

        Args:
            key: Cache key.
            value: Value to cache.
            ttl: Time-to-live in seconds. If None, uses default_ttl.

        Returns:
            True if successful.
        """
        actual_ttl = ttl if ttl is not None else self.default_ttl
        expiry = time.time() + actual_ttl if actual_ttl > 0 else None
        self._cache[key] = (value, expiry)
        return True

    def delete(self, key: str) -> bool:
        """Delete value from cache.

        Args:
            key: Cache key.

        Returns:
            True if key existed and was deleted, False otherwise.
        """
        if key in self._cache:
            del self._cache[key]
            return True
        return False

    def exists(self, key: str) -> bool:
        """Check if key exists in cache.

        Args:
            key: Cache key.

        Returns:
            True if key exists and not expired, False otherwise.
        """
        return self.get(key) is not None

    def clear(self) -> bool:
        """Clear all cached values.

        Returns:
            True if successful.
        """
        self._cache.clear()
        return True

    def get_many(self, keys: list[str]) -> dict[str, Any]:
        """Get multiple values from cache.

        Args:
            keys: List of cache keys.

        Returns:
            Dict of key-value pairs (only existing and non-expired).
        """
        result = {}
        for key in keys:
            value = self.get(key)
            if value is not None:
                result[key] = value
        return result

    def set_many(self, items: dict[str, Any], ttl: Optional[int] = None) -> bool:
        """Set multiple values in cache.

        Args:
            items: Dict of key-value pairs to cache.
            ttl: Time-to-live in seconds. If None, uses default_ttl.

        Returns:
            True if successful.
        """
        for key, value in items.items():
            self.set(key, value, ttl)
        return True

    def delete_many(self, keys: list[str]) -> int:
        """Delete multiple values from cache.

        Args:
            keys: List of cache keys.

        Returns:
            Number of keys deleted.
        """
        count = 0
        for key in keys:
            if self.delete(key):
                count += 1
        return count

    def get_stats(self) -> dict:
        """Get cache statistics.

        Returns:
            Dict with cache stats (size, etc).
        """
        # Clean up expired entries first
        current_time = time.time()
        expired_keys = [
            k for k, (_, expiry) in self._cache.items()
            if expiry is not None and current_time > expiry
        ]
        for key in expired_keys:
            del self._cache[key]

        return {
            "size": len(self._cache),
            "default_ttl": self.default_ttl,
        }