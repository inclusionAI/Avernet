"""Thread-safe in-process cache for query embeddings."""

from __future__ import annotations

import hashlib
import threading
import time
import unicodedata
from collections import OrderedDict, deque
from typing import Callable

from app.infra.public.embedding.real_embedding_provider import (
    RealEmbeddingProvider,
)


class _PendingEmbedding:
    """One unique pending query and its per-request completion event."""

    __slots__ = ("text", "key", "event", "vector", "error")

    def __init__(self, text: str, key: str) -> None:
        self.text = text
        self.key = key
        self.event = threading.Event()
        self.vector: list[float] | None = None
        self.error: BaseException | None = None


_EMBEDDING_CACHE_METRICS: dict[str, object] | None = None


def _get_embedding_cache_metrics() -> dict[str, object]:
    global _EMBEDDING_CACHE_METRICS
    if _EMBEDDING_CACHE_METRICS is not None:
        return _EMBEDDING_CACHE_METRICS

    from prometheus_client import Counter, Gauge, Histogram

    _EMBEDDING_CACHE_METRICS = {
        "hits": Counter(
            "bcsfuse_embedding_cache_hits_total",
            "Embedding cache hits",
            ["model"],
        ),
        "misses": Counter(
            "bcsfuse_embedding_cache_misses_total",
            "Embedding cache misses requiring a backend call",
            ["model"],
        ),
        "errors": Counter(
            "bcsfuse_embedding_cache_errors_total",
            "Failed embedding cache backend calls",
            ["model"],
        ),
        "evictions": Counter(
            "bcsfuse_embedding_cache_evictions_total",
            "Entries evicted by embedding cache capacity",
            ["model"],
        ),
        "entries": Gauge(
            "bcsfuse_embedding_cache_entries",
            "Embedding cache entries currently held",
            ["model"],
        ),
        "batch_calls": Counter(
            "bcsfuse_embedding_batch_calls_total",
            "Embedding backend calls produced by query batching",
            ["model"],
        ),
        "batched_queries": Counter(
            "bcsfuse_embedding_batched_queries_total",
            "Unique query embeddings sent in a batch call",
            ["model"],
        ),
        "batch_size": Histogram(
            "bcsfuse_embedding_batch_size",
            "Embedding query batch size sent to the backend",
            ["model"],
            buckets=(1, 2, 4, 8, 16, 32),
        ),
    }
    return _EMBEDDING_CACHE_METRICS


def normalize_embedding_query(text: str) -> str:
    """Normalize only Unicode- and whitespace-equivalent queries.

    Case folding and alias rewriting are intentionally omitted so a cache key
    never collapses inputs that a model could treat differently.
    """
    normalized = unicodedata.normalize("NFKC", text)
    return " ".join(normalized.split())


class CachedEmbeddingProvider(RealEmbeddingProvider):
    """Cache an exact model, dimension, and normalized query embedding.

    Calls are single-flight by cache key. The backend always receives the
    original text; normalization is used only to derive the cache key. Backend
    failures are not cached.
    """

    def __init__(
        self,
        backend: RealEmbeddingProvider,
        *,
        max_entries: int = 10000,
        ttl_seconds: float = 300.0,
        clock: Callable[[], float] = time.monotonic,
        max_query_batch_size: int = 32,
        query_batch_wait_seconds: float = 0.05,
        query_batch_peak_wait_seconds: float = 0.15,
        query_batch_peak_threshold: int = 8,
        max_query_batch_workers: int = 1,
    ) -> None:
        if max_entries <= 0:
            raise ValueError("embedding cache max_entries must be positive")
        if ttl_seconds < 0:
            raise ValueError("embedding cache ttl_seconds cannot be negative")
        if max_query_batch_size <= 0:
            raise ValueError(
                "embedding max_query_batch_size must be positive"
            )
        if query_batch_wait_seconds < 0:
            raise ValueError(
                "embedding query_batch_wait_seconds cannot be negative"
            )
        if query_batch_peak_wait_seconds < query_batch_wait_seconds:
            raise ValueError(
                "embedding query_batch_peak_wait_seconds must not be shorter than the short batch window"
            )
        if query_batch_peak_threshold < 1:
            raise ValueError(
                "embedding query_batch_peak_threshold must be positive"
            )
        if max_query_batch_workers <= 0:
            raise ValueError(
                "embedding max_query_batch_workers must be positive"
            )

        metrics = _get_embedding_cache_metrics()
        self._embedding_metrics = metrics
        self.backend = backend
        self.cache_max_entries = max_entries
        self.cache_ttl_seconds = ttl_seconds
        self.max_query_batch_size = max_query_batch_size
        self.query_batch_wait_seconds = query_batch_wait_seconds
        self.query_batch_peak_wait_seconds = query_batch_peak_wait_seconds
        self.query_batch_peak_threshold = query_batch_peak_threshold
        self.max_query_batch_workers = max_query_batch_workers
        self._model_label = str(getattr(backend._settings, "model", "unknown"))
        self._clock = clock
        self._cache_lock = threading.RLock()
        self._cache: OrderedDict[str, tuple[float, list[float]]] = OrderedDict()
        self._pending_condition = threading.Condition(self._cache_lock)
        self._pending_keys: dict[str, _PendingEmbedding] = {}
        self._pending_queue: deque[_PendingEmbedding] = deque()
        self._dispatcher_thread: threading.Thread | None = None
        self._batch_executor = None
        self._closed = False
        self._cache_hits = 0
        self._cache_misses = 0
        self._cache_errors = 0
        self._cache_evictions = 0

        super().__init__(settings=backend._settings)

    def _key(self, text: str) -> str:
        normalized = normalize_embedding_query(text)
        settings = self.backend._settings
        material = (
            f"{self._model_label}\0{int(settings.dimension)}\0{normalized}"
        ).encode("utf-8")
        return hashlib.sha256(material).hexdigest()

    def _record_hit(self) -> None:
        self._cache_hits += 1
        self._embedding_metrics["hits"].labels(self._model_label).inc()

    def _record_misses(self, count: int = 1) -> None:
        """Record a batch of unique misses without per-request registry locks."""

        if count <= 0:
            return
        self._cache_misses += count
        self._embedding_metrics["misses"].labels(self._model_label).inc(count)

    def _record_errors(self, count: int = 1) -> None:
        if count <= 0:
            return
        self._cache_errors += count
        self._embedding_metrics["errors"].labels(self._model_label).inc(count)

    def _record_eviction(self) -> None:
        self._cache_evictions += 1
        self._embedding_metrics["evictions"].labels(self._model_label).inc()

    def _sync_entry_gauge(self) -> None:
        self._embedding_metrics["entries"].labels(self._model_label).set(
            len(self._cache)
        )

    @property
    def stats(self) -> dict[str, float | int]:
        total = self._cache_hits + self._cache_misses
        ratio = float(self._cache_hits / total) if total else 0.0
        return {
            "cache_hits": self._cache_hits,
            "cache_misses": self._cache_misses,
            "cache_errors": self._cache_errors,
            "cache_evictions": self._cache_evictions,
            "cache_entries": len(self._cache),
            "cache_hit_ratio": ratio,
        }

    @property
    def pending_queries(self) -> int:
        """Expose bounded dispatcher backlog for tests and diagnostics."""

        return len(self._pending_queue)

    def embed(self, text: str) -> list[float]:
        key = self._key(text)
        hit_vector: list[float] | None = None
        missed = False
        entry_expired = False
        with self._pending_condition:
            if self._closed:
                raise RuntimeError("embedding cache is closed")
            now = self._clock()
            if self.cache_ttl_seconds > 0:
                entry = self._cache.get(key)
                if entry is not None:
                    expires_at, vector = entry
                    if now < expires_at:
                        self._cache.move_to_end(key)
                        hit_vector = list(vector)
                    else:
                        self._cache.pop(key, None)
                        entry_expired = True

            if hit_vector is None:
                pending = self._pending_keys.get(key)
                if pending is None:
                    pending = _PendingEmbedding(text=text, key=key)
                    self._pending_keys[key] = pending
                    self._pending_queue.append(pending)
                    missed = True
                    self._ensure_dispatcher_locked()
                    self._pending_condition.notify_all()
                else:
                    pending = pending
            else:
                pending = None

        # Metric registry updates can become expensive under a large cold burst.
        # Keep the shared pending condition short and record hit/miss bookkeeping
        # after the cache state transition is complete.
        if entry_expired:
            self._sync_entry_gauge()
        if hit_vector is not None:
            self._record_hit()
            return hit_vector
        assert pending is not None
        pending.event.wait()
        if pending.error is not None:
            raise pending.error
        vector = pending.vector
        if vector is None:
            raise RuntimeError("embedding batch dispatcher returned no vector")
        return list(vector)

    def _ensure_dispatcher_locked(self) -> None:
        if self._closed or self._dispatcher_thread is not None:
            return
        self._dispatcher_thread = threading.Thread(
            target=self._dispatch_pending,
            name=f"bcsfuse-embedding-batch-{id(self)}",
            daemon=True,
        )
        self._dispatcher_thread.start()

    def _dispatch_pending(self) -> None:
        while True:
            with self._pending_condition:
                while not self._pending_queue and not self._closed:
                    self._pending_condition.wait()
                if not self._pending_queue:
                    return

                if self.query_batch_wait_seconds > 0:
                    started_waiting_at = time.monotonic()
                    short_deadline = started_waiting_at + self.query_batch_wait_seconds
                    peak_deadline = started_waiting_at + self.query_batch_peak_wait_seconds
                    deadline = short_deadline
                    peak_window_used = False
                    while (
                        len(self._pending_queue) < self.max_query_batch_size
                        and not self._closed
                    ):
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            if (
                                not peak_window_used
                                and len(self._pending_queue) >= self.query_batch_peak_threshold
                            ):
                                peak_window_used = True
                                deadline = peak_deadline
                                remaining = deadline - time.monotonic()
                            if remaining <= 0:
                                break
                        self._pending_condition.wait(timeout=remaining)

                if self._closed and not self._pending_queue:
                    return

                batch: list[_PendingEmbedding] = []
                while self._pending_queue and len(batch) < self.max_query_batch_size:
                    batch.append(self._pending_queue.popleft())

            self._submit_batch_locked(batch)

    def _submit_batch_locked(self, batch: list[_PendingEmbedding]) -> None:
        if self._batch_executor is None:
            from concurrent.futures import ThreadPoolExecutor

            self._batch_executor = ThreadPoolExecutor(
                max_workers=self.max_query_batch_workers,
                thread_name_prefix=f"bcsfuse-embedding-backend-{id(self)}",
            )
        self._batch_executor.submit(self._execute_batch, batch)

    def _execute_batch(self, batch: list[_PendingEmbedding]) -> None:
        texts = [pending.text for pending in batch]
        self._record_misses(len(texts))
        self._embedding_metrics["batch_calls"].labels(self._model_label).inc()
        self._embedding_metrics["batched_queries"].labels(
            self._model_label
        ).inc(len(texts))
        self._embedding_metrics["batch_size"].labels(self._model_label).observe(
            len(texts)
        )

        try:
            vectors = self.backend.embed_batch(texts)
            if len(vectors) != len(texts):
                raise ValueError(
                    "embedding backend returned a mismatched batch count"
                )
        except BaseException as error:
            with self._pending_condition:
                for pending in batch:
                    self._pending_keys.pop(pending.key, None)
                for pending in batch:
                    pending.error = error
            self._record_errors(len(batch))
            for pending in batch:
                pending.event.set()
            return

        with self._pending_condition:
            for pending, vector in zip(batch, vectors):
                self._pending_keys.pop(pending.key, None)
                if self.cache_ttl_seconds > 0:
                    self._cache[pending.key] = (
                        self._clock() + self.cache_ttl_seconds,
                        list(vector),
                    )
                    self._cache.move_to_end(pending.key)
            if self.cache_ttl_seconds > 0:
                while len(self._cache) > self.cache_max_entries:
                    self._cache.popitem(last=False)
                    self._record_eviction()
                self._sync_entry_gauge()
        for pending, vector in zip(batch, vectors):
            pending.vector = list(vector)
            pending.event.set()

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        # Index bulk writes intentionally bypass the query cache.
        return self.backend.embed_batch(texts)

    def close(self) -> None:
        with self._pending_condition:
            self._closed = True
            self._pending_condition.notify_all()
            thread = self._dispatcher_thread
            executor = self._batch_executor
        if thread is not None:
            thread.join(timeout=2.0)
        if executor is not None:
            executor.shutdown(wait=True)
        try:
            self.backend.close()
        finally:
            super().close()
