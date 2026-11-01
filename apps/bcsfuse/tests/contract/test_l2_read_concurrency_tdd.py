"""TDD contract for bounded cross-pod L2 response-cache reads."""

from __future__ import annotations

import asyncio
import threading
from typing import Any


class BlockingSharedRead:
    """Record the maximum number of concurrent backend cache reads."""

    def __init__(self) -> None:
        self.active = 0
        self.max_active = 0
        self.reads = 0
        self._lock = threading.Lock()
        self.reads_started = threading.Event()
        self.two_reads_started = threading.Event()
        self._release_reads = threading.Event()

    def get(self, key: str, *, index_version: int) -> bytes | None:
        with self._lock:
            self.active += 1
            self.reads += 1
            self.max_active = max(self.max_active, self.active)
        self.reads_started.set()
        if self.active == 2:
            self.two_reads_started.set()
        self._release_reads.wait(timeout=1)
        with self._lock:
            self.active -= 1
        return b'{"status":"ok"}'

    def release(self) -> None:
        self._release_reads.set()


def test_unique_l2_reads_share_a_process_wide_bounded_reader() -> None:
    from app.adapters.http.shared_response_cache_reader import (
        BoundedSharedResponseCacheReader,
    )

    backend = BlockingSharedRead()
    reader = BoundedSharedResponseCacheReader(
        shared_response_cache=backend,
        timeout_seconds=1.0,
        max_concurrent_reads=2,
    )

    async def scenario() -> None:
        tasks = [
            asyncio.create_task(
                reader.get(
                    f"unique-key-{number}",
                    index_version=7,
                    operation="test_reader",
                )
            )
            for number in range(10)
        ]
        assert await asyncio.to_thread(backend.two_reads_started.wait, 1)
        await asyncio.sleep(0.05)
        assert backend.max_active == 2
        backend.release()
        payloads = await asyncio.gather(*tasks)
        assert all(payload == b'{"status":"ok"}' for payload in payloads)

    asyncio.run(scenario())
    assert backend.max_active <= 2
    assert backend.reads == 10


def test_composition_root_shares_the_bounded_reader_between_search_and_recommend() -> None:
    from app.adapters.http.recommend_response_cache import (
        RecommendationResponseCacheMiddleware,
    )
    from app.adapters.http.search_response_cache import SearchResponseCacheMiddleware
    from app.bootstrap.app_factory import create_bcsfuse_app
    from app.bootstrap.application_context import ApplicationContext
    from app.bootstrap.provider_registry import ProviderRegistry

    class Startup:
        async def initialize(self) -> None:
            return None

        async def shutdown(self) -> None:
            return None

    class Store:
        index_owner = "pod-a"
        index_version = 7
        index_lag_seconds = 0
        index_stale = False
        read_only = False

        def check_ready(self) -> bool:
            return True

    class SharedCache:
        def get(self, key: str, *, index_version: int) -> bytes | None:
            return None

        def store(
            self,
            key: str,
            payload: bytes,
            *,
            index_version: int,
            ttl_seconds: float,
        ) -> bool:
            return True

    registry = ProviderRegistry()
    registry.register("config", object())
    registry.register("secret_provider", object())
    registry.register("startup_provider", Startup())
    registry.register("vector_store", Store())
    registry.register("shared_response_cache", SharedCache())
    context = ApplicationContext(
        mode="runtime",
        startup_profile="opensource",
        registry=registry,
    )
    app = create_bcsfuse_app(context)

    def middleware(cls: type[Any]) -> Any:
        return next(item for item in app.user_middleware if item.cls is cls)

    search_reader = middleware(SearchResponseCacheMiddleware).kwargs[
        "shared_cache_reader"
    ]
    recommend_reader = middleware(
        RecommendationResponseCacheMiddleware
    ).kwargs["shared_cache_reader"]
    assert search_reader is not None
    assert search_reader is recommend_reader
