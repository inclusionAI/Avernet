"""Async single-flight cache for online query embeddings."""

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from collections.abc import Callable
from typing import Any

from app.infra.public.embedding.cached_embedding_provider import (
    _get_embedding_cache_metrics,
    normalize_embedding_query,
)


class CachedAsyncEmbeddingProvider:
    """Cache model/dimension/query embeddings without consuming worker threads.

    Cache keys intentionally preserve case, while Unicode NFKC and whitespace
    are normalized. Concurrent callers for the same key share one backend future;
    backend failures and cancellation are never cached.
    """

    def __init__(
        self,
        backend: Any,
        *,
        max_entries: int = 10000,
        ttl_seconds: float = 300.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if max_entries <= 0:
            raise ValueError("embedding cache max_entries must be positive")
        if ttl_seconds < 0:
            raise ValueError("embedding cache ttl_seconds cannot be negative")
        self.backend = backend
        self.cache_max_entries = max_entries
        self.cache_ttl_seconds = ttl_seconds
        settings = getattr(backend, "_settings", None)
        self._model_label = str(
            getattr(backend, "model", None)
            or getattr(settings, "model", None)
            or "unknown"
        )
        self._clock = clock
        self._metrics = _get_embedding_cache_metrics()
        self._lock = asyncio.Lock()
        self._cache: OrderedDict[str, tuple[float, list[float]]] = OrderedDict()
        self._pending: dict[str, asyncio.Future[list[float]]] = {}
        self._cache_hits = 0
        self._cache_misses = 0
        self._cache_evictions = 0
        self._closed = False

    @property
    def dimension(self) -> int:
        return int(self.backend.dimension)

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def stats(self) -> dict[str, float | int]:
        total = self._cache_hits + self._cache_misses
        return {
            "cache_hits": self._cache_hits,
            "cache_misses": self._cache_misses,
            "cache_evictions": self._cache_evictions,
            "cache_entries": len(self._cache),
            "cache_hit_ratio": float(self._cache_hits / total) if total else 0.0,
        }

    @property
    def pending_queries(self) -> int:
        return len(self._pending)

    def _record_hit(self) -> None:
        self._cache_hits += 1
        self._metrics["hits"].labels(self._model_label).inc()

    def _record_miss(self) -> None:
        self._cache_misses += 1
        self._metrics["misses"].labels(self._model_label).inc()

    def _record_eviction(self) -> None:
        self._cache_evictions += 1
        self._metrics["evictions"].labels(self._model_label).inc()

    def _sync_entry_gauge(self) -> None:
        self._metrics["entries"].labels(self._model_label).set(len(self._cache))

    async def embed(self, text: str) -> list[float]:
        if self._closed:
            raise RuntimeError("embedding cache provider is closed")
        key = f"{self._model_label}|{normalize_embedding_query(text)}"
        loop = asyncio.get_running_loop()
        existing_future: asyncio.Future[list[float]] | None = None
        leader_future: asyncio.Future[list[float]] | None = None

        async with self._lock:
            entry = self._cache.get(key)
            if self.cache_ttl_seconds > 0 and entry is not None:
                expires_at, vector = entry
                if self._clock() < expires_at:
                    self._cache.move_to_end(key)
                    self._record_hit()
                    self._sync_entry_gauge()
                    return list(vector)
                self._cache.pop(key, None)

            pending = self._pending.get(key)
            if pending is not None and not pending.done():
                existing_future = pending
            else:
                leader_future = loop.create_future()
                self._pending[key] = leader_future
                self._record_miss()

        if existing_future is not None:
            return list(await asyncio.shield(existing_future))

        assert leader_future is not None
        try:
            vector = await self.backend.embed(text)
        except BaseException as error:
            async with self._lock:
                if self._pending.get(key) is leader_future:
                    self._pending.pop(key, None)
                try:
                    leader_future.set_exception(error)
                except asyncio.InvalidStateError:
                    pass
            raise

        async with self._lock:
            if self._pending.get(key) is leader_future:
                self._pending.pop(key, None)
            expires_at = float("inf")
            if self.cache_ttl_seconds > 0:
                expires_at = self._clock() + self.cache_ttl_seconds
            self._cache[key] = (expires_at, list(vector))
            while len(self._cache) > self.cache_max_entries:
                self._cache.popitem(last=False)
                self._record_eviction()
            self._sync_entry_gauge()
            try:
                leader_future.set_result(list(vector))
            except asyncio.InvalidStateError:
                pass
        return list(vector)

    async def aclose(self) -> None:
        try:
            async with self._lock:
                self._closed = True
                pending = list(self._pending.values())
                self._pending.clear()
                self._cache.clear()
                for future in pending:
                    if not future.done():
                        future.set_exception(
                            RuntimeError("embedding cache provider is closed")
                        )
            close = getattr(self.backend, "aclose", None)
            if close is not None:
                await close()
        finally:
            self._closed = True


__all__ = ["CachedAsyncEmbeddingProvider"]
