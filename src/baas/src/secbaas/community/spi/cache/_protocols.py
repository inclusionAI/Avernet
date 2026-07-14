"""Cache plugin Protocol — key-value cache with TTL contract."""

from __future__ import annotations

from typing import Protocol


class CachePlugin(Protocol):
    """Plugin protocol for key-value cache operations.

    Implementations:
    - RealCachePlugin: wraps Layotto ZCache for production.
    - StubCachePlugin: in-memory dict with TTL support for tests.
    """

    def get(self, key: str) -> str | None:
        """Retrieve a cached value by key.

        Args:
            key: Cache key to look up.

        Returns:
            The cached string value, or None if the key does not exist
            or has expired.
        """
        ...

    def set(self, key: str, value: str, ttl_seconds: int) -> None:
        """Store a value in the cache with a TTL.

        Args:
            key: Cache key.
            value: String value to cache.
            ttl_seconds: Time-to-live in seconds. After this duration,
                the key is considered expired.
        """
        ...

    def close(self) -> None:
        """Release underlying cache resources.

        Implementations that manage external connections (Layotto ZCache,
        Redis, etc.) should clean up here. No-op by default for
        stateless testing stubs.
        """
        ...
