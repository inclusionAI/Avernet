"""Stub cache plugin — in-memory dict implementation for testing.

Provides a lightweight CachePlugin that stores values in an in-memory
dict with TTL-based expiry. Thread-safe via a lock.
"""

from __future__ import annotations

import threading
import time

from secbaas.community.spi.cache import CachePlugin


class _CacheEntry:
    """Internal cache entry with expiry support."""

    __slots__ = ("value", "expires_at")

    def __init__(self, value: str, expires_at: float) -> None:
        self.value = value
        self.expires_at = expires_at


class StubCachePlugin(CachePlugin):
    """In-memory dict-based cache plugin for testing.

    Supports TTL-based expiry via ``time.monotonic()``. Thread-safe
    via a reentrant lock. Includes a ``clear()`` helper for test
    teardown.
    """

    def __init__(self) -> None:
        self._store: dict[str, _CacheEntry] = {}
        self._lock = threading.Lock()

    def get(self, key: str) -> str | None:
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            if entry.expires_at < time.monotonic():
                del self._store[key]
                return None
            return entry.value

    def set(self, key: str, value: str, ttl_seconds: int) -> None:
        with self._lock:
            self._store[key] = _CacheEntry(value, time.monotonic() + ttl_seconds)

    # ── Test helpers ─────────────────────────────────────────────────────

    def close(self) -> None:
        with self._lock:
            self._store.clear()

    def clear(self) -> None:
        """Remove all entries (convenience for test teardown)."""
        with self._lock:
            self._store.clear()
