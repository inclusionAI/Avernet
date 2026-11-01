"""Bounded result cache for the unified v1 search application service."""

from __future__ import annotations

import hashlib
import json
import threading
import time
import unicodedata
from collections import OrderedDict
from contextlib import asynccontextmanager
from copy import deepcopy
from typing import Any, Callable

from app.infra.public.observability.search_result_cache_metrics import (
    record_search_result_cache_eviction,
    record_search_result_cache_hit,
    record_search_result_cache_miss,
    set_search_result_cache_entries,
)

IndexVersionProvider = Callable[[], Any]
Clock = Callable[[], float]


class AgentSearchResultCache:
    """Process-local, index-version-aware L1 cache for successful results.

    The cache is shared by search and recommendation commands. A cache instance
    is deliberately owned by the composition root so routes cannot create a
    fresh ineffective cache for each request.
    """

    def __init__(
        self,
        *,
        index_version_provider: IndexVersionProvider,
        max_entries: int = 1000,
        ttl_seconds: float = 30.0,
        clock: Clock = time.monotonic,
    ) -> None:
        if max_entries <= 0:
            raise ValueError("result cache max_entries must be positive")
        if ttl_seconds < 0:
            raise ValueError("result cache ttl_seconds must be non-negative")
        if index_version_provider is None:
            raise ValueError("result cache index_version_provider is required")

        self._index_version_provider = index_version_provider
        self._max_entries = max_entries
        self._ttl_seconds = ttl_seconds
        self._clock = clock
        self._entries: OrderedDict[str, tuple[float, Any]] = OrderedDict()
        self._entry_lock = threading.RLock()
        self._inflight: dict[str, Any] = {}
        self._hits = 0
        self._misses = 0

    @classmethod
    def from_context(cls, context: Any) -> "AgentSearchResultCache | None":
        """Compose explicit capacity, freshness and enablement at startup."""

        enabled = bool(
            _config_get(context, "search.result_cache.enabled", True, "get_bool")
        )
        if not enabled:
            raise RuntimeError(
                "search.result_cache.enabled must be true for serving; "
                "disable the cache only by making the deployment invalid"
            )

        max_entries = int(
            _config_get(
                context, "search.result_cache.max_entries", 1000, "get_int"
            )
        )
        ttl_seconds = float(
            _config_get(
                context, "search.result_cache.ttl_seconds", 30.0, "get_float"
            )
        )

        def current_index_version() -> Any:
            store = context.registry.get("vector_store")
            return getattr(store, "index_version", None)

        return cls(
            index_version_provider=current_index_version,
            max_entries=max_entries,
            ttl_seconds=ttl_seconds,
        )

    @property
    def enabled(self) -> bool:
        return self._ttl_seconds > 0

    @property
    def stats(self) -> dict[str, int | float]:
        with self._entry_lock:
            return {
                "hits": self._hits,
                "misses": self._misses,
                "entries": len(self._entries),
                "max_entries": self._max_entries,
                "ttl_seconds": self._ttl_seconds,
            }

    def normalize_query(self, query: str) -> str:
        """Use the conservative core normalization shared by cache and retrieval."""

        return " ".join(unicodedata.normalize("NFKC", query).split())

    def current_index_version(self) -> int | None:
        if not self.enabled:
            return None
        try:
            raw_version = self._index_version_provider()
            version = int(raw_version)
        except Exception:
            return None
        if version < 0:
            return None
        return version

    def key(self, operation: str, *, index_version: int, material: Any) -> str:
        payload = json.dumps(
            {
                "operation": operation,
                "index_version": index_version,
                "material": material,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=_json_fallback,
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    def get(self, key: str, *, operation: str = "candidate") -> Any | None:
        if not self.enabled:
            return None
        with self._entry_lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            expires_at, value = entry
            if self._clock() >= expires_at:
                self._entries.pop(key, None)
                record_search_result_cache_eviction("v1", operation=operation)
                set_search_result_cache_entries(len(self._entries), "v1")
                return None
            self._entries.move_to_end(key)
            self._hits += 1
        record_search_result_cache_hit("v1", operation=operation)
        return deepcopy(value)

    def store_and_get(
        self, key: str, value: Any, *, operation: str = "candidate"
    ) -> Any | None:
        """Store and re-read a projection while allowing middleware fail-open."""

        self.store(key, value, operation=operation)
        return self.get(key, operation=operation)

    def get_without_metrics(self, key: str) -> Any | None:
        """Recheck a value inside singleflight without counting a second hit."""

        if not self.enabled:
            return None
        with self._entry_lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            expires_at, value = entry
            if self._clock() >= expires_at:
                self._entries.pop(key, None)
                record_search_result_cache_eviction("v1", operation="candidate")
                set_search_result_cache_entries(len(self._entries), "v1")
                return None
            return deepcopy(value)

    def record_miss(self, *, operation: str = "candidate") -> None:
        with self._entry_lock:
            self._misses += 1
        record_search_result_cache_miss("v1", operation=operation)

    def store(self, key: str, value: Any, *, operation: str = "candidate") -> None:
        if not self.enabled:
            return
        value_copy = deepcopy(value)
        with self._entry_lock:
            self._entries[key] = (self._clock() + self._ttl_seconds, value_copy)
            self._entries.move_to_end(key)
            while len(self._entries) > self._max_entries:
                self._entries.popitem(last=False)
                record_search_result_cache_eviction("v1", operation=operation)
            set_search_result_cache_entries(len(self._entries), "v1")

    @asynccontextmanager
    async def singleflight(self, key: str):
        """Collapse identical requests on the same event loop."""

        lock = self._inflight.setdefault(key, _new_asyncio_lock())
        await lock.acquire()
        try:
            yield
        finally:
            lock.release()
            if not lock.locked() and self._inflight.get(key) is lock:
                self._inflight.pop(key, None)


def _new_asyncio_lock() -> Any:
    import asyncio

    return asyncio.Lock()


def _json_fallback(value: Any) -> Any:
    if isinstance(value, (set, frozenset, tuple)):
        return list(value)
    return str(value)


def _config_get(
    context: Any, key: str, default: Any, method_name: str
) -> Any:
    config = getattr(context, "config", None)
    method = getattr(config, method_name, None)
    if method is None:
        return default
    return method(key, default)


__all__ = ["AgentSearchResultCache"]
