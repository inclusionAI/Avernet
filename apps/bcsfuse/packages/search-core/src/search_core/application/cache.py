"""Optional local caches; catalog changes and DRM replacements invalidate results."""

import asyncio
import hashlib
import inspect
import json
import logging
import math
from contextlib import contextmanager
from threading import RLock

from search_core.core.memory_cache import MemoryCache
from search_core.core.timing import stage_timing

logger = logging.getLogger(__name__)
_MANUAL_REASONS = {"keyword_disabled", "vector_disabled", "reranker_disabled"}


def _key(value):
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, ensure_ascii=False, separators=(",", ":")
        ).encode()
    ).digest()


class SearchCaches:
    """One application owns the caches and a revision for locally observed data.

    Mutation scopes never hold a lock across index/DB/model work. They suppress
    result caching until the mutation finishes and reject late old-query fills.
    Query vectors are independent of Card/index changes.
    """

    def __init__(
        self,
        *,
        result_entries,
        result_bytes,
        embedding_entries,
        embedding_bytes,
        metadata_cache=None,
    ):
        self._limits = (
            (result_entries, result_bytes),
            (embedding_entries, embedding_bytes),
            (1024, 16 * 1024 * 1024),
            (result_entries, result_bytes),
        )
        self._lock = RLock()
        self._revision = self._mutations = 0
        self._caches = ()
        self._logged_stats = {}
        self._metadata_cache = metadata_cache
        # The retrieval variants share one event loop and the same Card cache.
        self.pending_card_metadata = {}

    def configure(self, policy):
        caches = tuple(
            MemoryCache(
                enabled=policy.enabled and p.enabled,
                # MemoryCache requires a positive TTL, even when disabled.
                ttl_ms=p.ttl_ms or 1,
                max_entries=p.max_entries if p.max_entries is not None else n,
                max_bytes=(
                    p.max_size_mb * 1024 * 1024 if p.max_size_mb is not None else b
                ),
            )
            for p, (n, b) in zip(
                (
                    policy.result,
                    policy.query_embedding,
                    policy.card_metadata,
                    policy.default_candidates,
                ),
                self._limits,
            )
        )
        with self._lock:
            for old in self._caches:
                old.retire()
            if self._metadata_cache is not None:
                self._metadata_cache.retire()
            self._caches = caches
            self._logged_stats.clear()
            self._metadata_cache = caches[2]
            self._revision += 1
        return caches[:3]

    @property
    def candidate_ids(self):
        return self._caches[3]

    def _clear_catalog_caches(self):
        if self._caches:
            self._caches[0].clear()
            self._caches[3].clear()
        if self._metadata_cache is not None:
            self._metadata_cache.clear()

    def revision(self):
        with self._lock:
            return None if self._mutations else self._revision

    @contextmanager
    def changing(self):
        with self._lock:
            self._mutations += 1
            self._revision += 1
            self._clear_catalog_caches()
        try:
            yield
        finally:
            with self._lock:
                self._mutations -= 1
                self._revision += 1
                self._clear_catalog_caches()

    def put_result(self, cache, revision, key, value):
        with self._lock:
            if not self._mutations and self._revision == revision:
                cache.put(key, value)

    def log_stats(self):
        with self._lock:
            caches = self._caches
        names = ("result", "query_embedding", "card_metadata", "default_candidates")
        for name, cache in zip(names, caches):
            stats = cache.stats()
            with self._lock:
                if caches is not self._caches:
                    return
                if not stats["enabled"] or stats == self._logged_stats.get(name):
                    continue
                self._logged_stats[name] = stats
            logger.info(json.dumps({"event": "search_cache", "cache": name, **stats}))



class CachedRetrieval:
    """Called after request validation, before the existing final eligibility check."""

    def __init__(self, retrieval, cache, state, *, namespace=()):
        self.retrieval, self.cache, self.state = retrieval, cache, state
        self.namespace = namespace

    async def retrieve(self, request):
        revision = self.state.revision()
        if revision is None or not self.cache.enabled:
            return await self.retrieval.retrieve(request)
        with stage_timing("result_cache_read") as stage:
            # Search/recommend currently share identical validated default semantics.
            key = _key(
                [
                    revision,
                    self.namespace,
                    request.query,
                    request.limit,
                    {k: sorted(v) for k, v in request.filters.items()},
                ]
            )
            hit = self.cache.get(key)
            if hit is not None and self.state.revision() == revision:
                stage["cache"] = "hit"
                return hit
            stage["cache"] = "miss"
        result = await self.retrieval.retrieve(request)
        if (result.status == "ok" or result.reasons) and set(
            result.reasons
        ) <= _MANUAL_REASONS:
            with stage_timing("result_cache_write"):
                self.state.put_result(self.cache, revision, key, result)
        return result


class CachedEmbedding:
    """Cache valid vectors and share overlapping calls within this instance."""

    def __init__(self, provider, cache, embedding_key, *, singleflight_enabled=True):
        self.provider, self.cache = provider, cache
        self.dimension = provider.dimension
        self.embedding_key = embedding_key
        self._singleflight_enabled = singleflight_enabled
        self._pending, self._waiters = {}, {}

    async def embed(self, text):
        with stage_timing("embedding_cache_read") as stage:
            key = _key([self.embedding_key, self.dimension, text])
            hit = self.cache.get(key)
            if hit is not None:
                stage["cache"] = "hit"
                return list(hit)
            stage["cache"] = "miss"
        if not self._singleflight_enabled:
            return list(await self._embed(text, key))
        task = self._pending.get(key)
        if task is None or task.done():
            task = asyncio.create_task(self._embed(text, key))
            self._pending[key] = task
            self._waiters[task] = 0
        # No await between lookup and registration: callers share one event loop.
        self._waiters[task] += 1
        try:
            return list(await asyncio.shield(task))
        finally:
            self._waiters[task] -= 1
            if not self._waiters[task]:
                del self._waiters[task]
                if self._pending.get(key) is task:
                    del self._pending[key]
                if not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                elif not task.cancelled():
                    task.exception()  # Consume failures even if the last waiter cancelled.

    async def _embed(self, text, key):
        if inspect.iscoroutinefunction(self.provider.embed):
            values = await self.provider.embed(text)
        else:
            values = await asyncio.to_thread(self.provider.embed, text)
        if len(values) != self.dimension or any(not math.isfinite(v) for v in values):
            raise ValueError("invalid query embedding")
        values = tuple(values)
        with stage_timing("embedding_cache_write"):
            self.cache.put(key, values)
        return values
