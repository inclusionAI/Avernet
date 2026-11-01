"""TDD for batching concurrent query embeddings through the shared cache."""

from __future__ import annotations

import threading
import time
from typing import Any

import pytest

from app.infra.embedding.config.embedding_settings import EmbeddingSettings
from app.infra.public.embedding.cached_embedding_provider import (
    CachedEmbeddingProvider,
)
from app.infra.public.embedding.real_embedding_provider import (
    RealEmbeddingProvider,
)


class RecordingEmbeddingBackend(RealEmbeddingProvider):
    """Record batch calls while preserving ordinary embed semantics."""

    def __init__(self) -> None:
        super().__init__(
            settings=EmbeddingSettings(
                base_url="https://models.example.test/v1",
                auth_token="test-only",
                model="query-batching",
                dimension=64,
            ),
            max_retries=0,
        )
        self.batches: list[list[str]] = []
        self.lock = threading.Lock()
        self.fail_next = False
        self.first_dispatch_entered = threading.Event()
        self.dispatch_release = threading.Event()

    def embed(self, text: str) -> list[float]:
        with self.lock:
            self.batches.append([text])
        if self.fail_next:
            self.fail_next = False
            raise RuntimeError("backend unavailable")
        return [float(len(text)), 1.0]

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        with self.lock:
            self.batches.append(list(texts))
            first = len(self.batches) == 1
            fail = self.fail_next
        if first:
            self.first_dispatch_entered.set()
            self.dispatch_release.wait(timeout=2)
        if fail:
            raise RuntimeError("backend unavailable")
        return [[float(len(text)), 1.0] for text in texts]


def provider(
    backend: RealEmbeddingProvider,
    *,
    max_query_batch_size: int,
) -> CachedEmbeddingProvider:
    return CachedEmbeddingProvider(
        backend,
        max_entries=128,
        ttl_seconds=300.0,
        max_query_batch_size=max_query_batch_size,
        max_query_batch_workers=2,
    )


def test_concurrent_unique_queries_share_bounded_backend_batches() -> None:
    backend = RecordingEmbeddingBackend()
    instance = provider(backend, max_query_batch_size=8)
    results: dict[str, list[float]] = {}
    result_lock = threading.Lock()
    start = threading.Event()
    threads = [
        threading.Thread(
            target=lambda text=text: _call_and_store(
                instance,
                text,
                results,
                result_lock,
                start,
            )
        )
        for text in (f"query-{index}" for index in range(32))
    ]
    for thread in threads:
        thread.start()
    start.set()

    assert backend.first_dispatch_entered.wait(timeout=2)
    backend.dispatch_release.set()
    for thread in threads:
        thread.join(timeout=3)

    assert all(not thread.is_alive() for thread in threads)
    assert results == {
        f"query-{index}": [float(len(f"query-{index}")), 1.0]
        for index in range(32)
    }
    assert len(backend.batches) == 4
    assert max(len(batch) for batch in backend.batches) <= 8
    instance.close()


def test_identical_concurrent_queries_take_one_slot_per_batch() -> None:
    backend = RecordingEmbeddingBackend()
    instance = provider(backend, max_query_batch_size=16)
    start = threading.Event()
    threads = [
        threading.Thread(
            target=lambda: _call_and_start(instance, "same query", start)
        )
        for _ in range(12)
    ]
    for thread in threads:
        thread.start()
    start.set()

    assert backend.first_dispatch_entered.wait(timeout=2)
    backend.dispatch_release.set()
    for thread in threads:
        thread.join(timeout=3)

    assert all(not thread.is_alive() for thread in threads)
    assert len(backend.batches[0]) == 1
    assert all(backend.batches[0][0] == "same query" for _ in range(1))
    instance.close()


def test_duplicate_arriving_during_dispatch_waits_for_same_batch() -> None:
    backend = RecordingEmbeddingBackend()
    instance = provider(backend, max_query_batch_size=16)
    first_started = threading.Event()
    late_vectors: list[list[float]] = []
    late_lock = threading.Lock()
    late_started = threading.Event()

    def call_first() -> None:
        first_started.set()
        instance.embed("same query")

    def call_late() -> None:
        late_started.set()
        vector = instance.embed("same query")
        with late_lock:
            late_vectors.append(vector)

    first = threading.Thread(target=call_first)
    first.start()

    assert backend.first_dispatch_entered.wait(timeout=2)
    late = threading.Thread(target=call_late)
    late.start()
    assert late_started.wait(timeout=2)
    backend.dispatch_release.set()
    first.join(timeout=3)
    late.join(timeout=3)

    assert all(not thread.is_alive() for thread in (first, late))
    assert backend.batches == [["same query"]]
    assert late_vectors == [[float(len("same query")), 1.0]]
    instance.close()


def test_batch_failure_wakes_all_calls_and_is_not_cached() -> None:
    backend = RecordingEmbeddingBackend()
    backend.fail_next = True
    instance = provider(backend, max_query_batch_size=16)
    start = threading.Event()
    errors: list[str] = []
    error_lock = threading.Lock()

    threads = [
        threading.Thread(
            target=lambda: _fail_and_start(instance, "broken query", errors, error_lock, start)
        )
        for _ in range(4)
    ]
    for thread in threads:
        thread.start()
    start.set()

    assert backend.first_dispatch_entered.wait(timeout=2)
    backend.dispatch_release.set()
    for thread in threads:
        thread.join(timeout=3)

    assert all(not thread.is_alive() for thread in threads)
    assert len(errors) == 4
    assert instance.stats["cache_errors"] == 1
    assert instance.stats["cache_entries"] == 0
    instance.close()


def test_serial_misses_still_use_one_element_batches_and_cache_hits() -> None:
    backend = RecordingEmbeddingBackend()
    instance = provider(backend, max_query_batch_size=8)

    first = instance.embed("serial query")
    second = instance.embed("serial query")

    assert first == second == [12.0, 1.0]
    instance.close()


def test_embed_after_close_fails_fast() -> None:
    backend = RecordingEmbeddingBackend()
    instance = provider(backend, max_query_batch_size=8)

    instance.close()

    with pytest.raises(RuntimeError, match="embedding cache is closed"):
        instance.embed("closed query")


def _wait_until(
    predicate: Any,
    *,
    timeout: float,
    on_timeout: Any,
) -> None:
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    on_timeout()


def _call_and_store(
    provider: CachedEmbeddingProvider,
    text: str,
    results: dict[str, list[float]],
    result_lock: threading.Lock,
    start: threading.Event,
) -> None:
    start.wait()
    vector = provider.embed(text)
    with result_lock:
        results[text] = vector


def _call_and_start(
    provider: CachedEmbeddingProvider,
    text: str,
    start: threading.Event,
) -> None:
    start.wait()
    provider.embed(text)


def _fail_and_start(
    provider: CachedEmbeddingProvider,
    text: str,
    errors: list[str],
    error_lock: threading.Lock,
    start: threading.Event,
) -> None:
    start.wait()
    try:
        provider.embed(text)
    except RuntimeError:
        with error_lock:
            errors.append(text)
    else:
        raise AssertionError("failed batch was swallowed")


def test_query_coalescing_window_collects_unique_queries_before_dispatch() -> None:
    backend = RecordingEmbeddingBackend()
    instance = CachedEmbeddingProvider(
        backend,
        max_entries=128,
        ttl_seconds=300.0,
        max_query_batch_size=8,
        query_batch_wait_seconds=0.05,
    )
    started = threading.Event()

    def call(text: str) -> None:
        started.set()
        instance.embed(text)

    first = threading.Thread(target=call, args=("first query",))
    first.start()
    assert started.wait(timeout=1)

    time.sleep(0.01)
    assert backend.first_dispatch_entered.is_set() is False
    assert instance.pending_queries == 1

    second_started = threading.Event()

    def call_second() -> None:
        second_started.set()
        instance.embed("second query")

    second = threading.Thread(target=call_second)
    second.start()
    assert second_started.wait(timeout=1)
    time.sleep(0.01)
    assert backend.first_dispatch_entered.is_set() is False
    assert instance.pending_queries == 2

    assert backend.first_dispatch_entered.wait(timeout=1)
    backend.dispatch_release.set()
    first.join(timeout=1)
    second.join(timeout=1)

    assert backend.batches == [["first query", "second query"]]
    instance.close()


def test_query_batch_defaults_match_high_capacity_search_policy() -> None:
    provider = CachedEmbeddingProvider(RecordingEmbeddingBackend())

    try:
        assert provider.max_query_batch_size == 32
        assert provider.query_batch_wait_seconds == 0.05
        assert provider.query_batch_peak_wait_seconds == 0.15
        assert provider.query_batch_peak_threshold == 8
        assert provider.max_query_batch_workers == 1
    finally:
        provider.close()


def test_peak_load_extends_the_batching_window_to_fill_target_batches() -> None:
    backend = RecordingEmbeddingBackend()
    instance = CachedEmbeddingProvider(
        backend,
        max_query_batch_size=100,
        query_batch_wait_seconds=0.05,
        query_batch_peak_wait_seconds=0.3,
        query_batch_peak_threshold=8,
        max_query_batch_workers=1,
    )
    start = threading.Event()
    threads = [
        threading.Thread(
            target=lambda text=text: _call_and_start(
                instance,
                text,
                start,
            )
        )
        for text in (f"peak-{index}" for index in range(32))
    ]
    for thread in threads:
        thread.start()
    start_time = time.perf_counter()
    start.set()

    try:
        assert backend.first_dispatch_entered.wait(timeout=1)
        dispatch_elapsed = time.perf_counter() - start_time
        assert dispatch_elapsed >= 0.2
        assert len(backend.batches[0]) == 32
    finally:
        backend.dispatch_release.set()
        for thread in threads:
            thread.join(timeout=2)
    assert all(not thread.is_alive() for thread in threads)
    instance.close()


def test_single_cold_query_does_not_wait_for_the_peak_window() -> None:
    backend = RecordingEmbeddingBackend()
    instance = CachedEmbeddingProvider(
        backend,
        max_query_batch_size=100,
        query_batch_wait_seconds=0.05,
        query_batch_peak_wait_seconds=0.3,
        query_batch_peak_threshold=8,
        max_query_batch_workers=1,
    )
    started = threading.Event()
    thread = threading.Thread(target=_call_and_start, args=(instance, "cold query", started))
    thread.start()
    start_time = time.perf_counter()
    started.set()

    try:
        assert backend.first_dispatch_entered.wait(timeout=1)
        dispatch_elapsed = time.perf_counter() - start_time
        assert dispatch_elapsed < 0.2
        assert backend.batches[0] == ["cold query"]
    finally:
        backend.dispatch_release.set()
        thread.join(timeout=2)
    assert not thread.is_alive()
    instance.close()


def test_query_coalescing_window_can_be_disabled() -> None:
    backend = RecordingEmbeddingBackend()
    instance = CachedEmbeddingProvider(
        backend,
        max_entries=128,
        ttl_seconds=300.0,
        max_query_batch_size=8,
        query_batch_wait_seconds=0.0,
    )
    instance.embed("single query")

    assert backend.batches == [["single query"]]
    instance.close()


def test_parallel_query_batches_do_not_serialize_on_one_backend_call() -> None:
    class ParallelBlockingBackend(RecordingEmbeddingBackend):
        def __init__(self) -> None:
            super().__init__()
            self.dispatches = threading.Event()
            self.dispatch_count = 0
            self.local_lock = threading.Lock()
            self.batch_release = threading.Event()

        def embed_batch(self, texts: list[str]) -> list[list[float]]:
            with self.local_lock:
                self.dispatch_count += 1
                if self.dispatch_count == 2:
                    self.dispatches.set()
            assert self.batch_release.wait(timeout=2)
            return [[float(len(text)), 1.0] for text in texts]

    backend = ParallelBlockingBackend()
    instance = CachedEmbeddingProvider(
        backend,
        max_entries=128,
        ttl_seconds=300.0,
        max_query_batch_size=1,
        query_batch_wait_seconds=0.0,
        max_query_batch_workers=2,
    )
    start = threading.Event()
    threads = [
        threading.Thread(
            target=lambda text=text: _call_and_store(
                instance,
                text,
                {},
                threading.Lock(),
                start,
            )
        )
        for text in ("parallel-first", "parallel-second")
    ]
    for thread in threads:
        thread.start()
    start.set()

    try:
        assert backend.dispatches.wait(timeout=1)
    finally:
        backend.batch_release.set()
    for thread in threads:
        thread.join(timeout=2)

    assert all(not thread.is_alive() for thread in threads)
    assert backend.dispatch_count == 2
    instance.close()


def test_miss_metrics_do_not_serialize_unique_queries_on_the_pending_lock() -> None:
    """Metric bookkeeping must not turn unique misses into a global critical path."""

    import concurrent.futures

    backend = RecordingEmbeddingBackend()
    instance = CachedEmbeddingProvider(
        backend,
        max_entries=128,
        ttl_seconds=0.0,
        max_query_batch_size=32,
        query_batch_wait_seconds=0.0,
        max_query_batch_workers=1,
    )
    original_record_misses = instance._record_misses
    miss_batches: list[int] = []

    def slow_metric(count: int) -> None:
        time.sleep(0.005)
        original_record_misses(count)
        miss_batches.append(count)

    instance._record_misses = slow_metric
    backend.dispatch_release.set()
    texts = [f"miss-metric-{index}" for index in range(32)]

    start = threading.Event()
    with concurrent.futures.ThreadPoolExecutor(max_workers=32) as pool:

        def embed_after_start(text: str) -> list[float]:
            start.wait()
            return instance.embed(text)

        futures = [pool.submit(embed_after_start, text) for text in texts]
        # Exclude worker-thread creation from the measurement; only the
        # post-release embedding critical path is timed.
        started = time.perf_counter()
        start.set()
        vectors = [future.result() for future in futures]
    elapsed = time.perf_counter() - started

    assert len(vectors) == 32
    assert sum(miss_batches) == 32
    assert all(0 < count <= 32 for count in miss_batches)
    assert elapsed < 0.12, f"metric recording serialized misses: {elapsed:.3f}s"
    instance.close()
