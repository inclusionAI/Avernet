"""Process-local last-known-good and short-lived negative outcomes."""

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

from app.infra.public.observability.search_dependency_metrics import (
    record_agent_search_negative_cache_hit,
    record_agent_search_negative_cache_store,
    record_agent_search_last_known_good_hit,
    record_agent_search_last_known_good_store,
)

IndexVersionProvider = Callable[[], Any]
Clock = Callable[[], float]


class AgentSearchResilienceCache:
    """Index-version-aware cached outcomes for dependency degradation.

    Last-known-good stores only a fully successful result and is served with an
    explicit stale marker. The negative cache stores only short-lived terminal
    ``fallback_unavailable`` outcomes so an identical request cannot repeatedly
    tcish an already failed backend during an outage.
    """

    def __init__(
        self,
        *,
        index_version_provider: IndexVersionProvider,
        max_entries: int = 10000,
        last_known_good_ttl_seconds: float = 600.0,
        negative_ttl_seconds: float = 10.0,
        clock: Clock | None = None,
    ) -> None:
        if max_entries <= 0:
            raise ValueError("resilience cache max_entries must be positive")
        if last_known_good_ttl_seconds < 0:
            raise ValueError(
                "last_known_good_ttl_seconds must be non-negative"
            )
        if negative_ttl_seconds < 0:
            raise ValueError("negative_ttl_seconds must be non-negative")
        if index_version_provider is None:
            raise ValueError("resilience cache index_version_provider is required")

        self._index_version_provider = index_version_provider
        self._max_entries = max_entries
        self._last_known_good_ttl_seconds = last_known_good_ttl_seconds
        self._negative_ttl_seconds = negative_ttl_seconds
        self._clock = clock or time.monotonic
        self._last_known_good: OrderedDict[str, tuple[float, Any]] = OrderedDict()
        self._negative: OrderedDict[str, tuple[float, Any]] = OrderedDict()
        self._inflight: dict[str, Any] = {}
        self._entry_lock = threading.RLock()

    @classmethod
    def from_context(cls, context: Any) -> "AgentSearchResilienceCache | None":
        enabled = _config_bool(
            context, "search.resilience.enabled", True
        )
        if not enabled:
            return None

        def current_index_version() -> Any:
            store = context.registry.get("vector_store")
            return getattr(store, "index_version", None)

        return cls(
            index_version_provider=current_index_version,
            max_entries=_config_int(
                context, "search.resilience.max_entries", 10000
            ),
            last_known_good_ttl_seconds=_config_float(
                context,
                "search.resilience.last_known_good.ttl_seconds",
                600.0,
            ),
            negative_ttl_seconds=_config_float(
                context,
                "search.resilience.negative.ttl_seconds",
                10.0,
            ),
        )

    def current_index_version(self) -> int | None:
        try:
            version_value = self._index_version_provider()
            version = int(version_value)
        except Exception:
            return None
        if version_value is None or version < 0:
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

    def normalize_query(self, query: str) -> str:
        """Use the core normalization shared by retrieval and cache keys."""

        if not isinstance(query, str):
            raise ValueError("query must be a string")
        return " ".join(unicodedata.normalize("NFKC", query).split())

    def get_last_known_good(self, key: str) -> tuple[Any, float] | None:
        entry = self._expired_or_get(self._last_known_good, key)
        if entry is None:
            return None
        expires_at, value = entry
        stored_at = expires_at - self._last_known_good_ttl_seconds
        age_seconds = max(0.0, self._clock() - stored_at)
        record_agent_search_last_known_good_hit()
        # Age is measured from store time, even when a zero TTL disables it.
        return deepcopy(value), age_seconds

    def store_last_known_good(self, key: str, value: Any) -> None:
        if self._last_known_good_ttl_seconds <= 0:
            return
        record_agent_search_last_known_good_store()
        self._store(
            self._last_known_good,
            key,
            value,
            self._last_known_good_ttl_seconds,
        )

    def get_negative(self, key: str) -> Any | None:
        entry = self._expired_or_get(self._negative, key)
        if entry is None:
            return None
        record_agent_search_negative_cache_hit()
        return deepcopy(entry[1])

    def store_negative(self, key: str, value: Any) -> None:
        if self._negative_ttl_seconds <= 0:
            return
        record_agent_search_negative_cache_store()
        self._store(self._negative, key, value, self._negative_ttl_seconds)

    @property
    def last_known_good_entries(self) -> int:
        with self._entry_lock:
            return len(self._last_known_good)

    @property
    def negative_entries(self) -> int:
        with self._entry_lock:
            return len(self._negative)

    @asynccontextmanager
    async def singleflight(self, key: str):
        lock = self._inflight.setdefault(key, _new_asyncio_lock())
        await lock.acquire()
        try:
            yield
        finally:
            lock.release()
            if not lock.locked() and self._inflight.get(key) is lock:
                self._inflight.pop(key, None)

    def _expired_or_get(
        self, entries: OrderedDict[str, tuple[float, Any]], key: str
    ) -> tuple[float, Any] | None:
        with self._entry_lock:
            entry = entries.get(key)
            if entry is None:
                return None
            expires_at, value = entry
            if self._clock() >= expires_at:
                entries.pop(key, None)
                return None
            entries.move_to_end(key)
            return expires_at, value

    def _store(
        self,
        entries: OrderedDict[str, tuple[float, Any]],
        key: str,
        value: Any,
        ttl_seconds: float,
    ) -> None:
        value_copy = deepcopy(value)
        with self._entry_lock:
            entries[key] = (self._clock() + ttl_seconds, value_copy)
            entries.move_to_end(key)
            while len(entries) > self._max_entries:
                entries.popitem(last=False)



def _new_asyncio_lock() -> Any:
    import asyncio

    return asyncio.Lock()


def _json_fallback(value: Any) -> Any:
    if isinstance(value, (set, frozenset, tuple)):
        return list(value)
    return str(value)


def _config_get(context: Any, key: str, default: Any, method_name: str) -> Any:
    config = getattr(context, "config", None)
    method = getattr(config, method_name, None)
    if method is None:
        return default
    return method(key, default)


def _config_int(context: Any, key: str, default: int) -> int:
    return int(_config_get(context, key, default, "get_int"))


def _config_float(context: Any, key: str, default: float) -> float:
    return float(_config_get(context, key, default, "get_float"))


def _config_bool(context: Any, key: str, default: bool) -> bool:
    return bool(_config_get(context, key, default, "get_bool"))


__all__ = ["AgentSearchResilienceCache"]
