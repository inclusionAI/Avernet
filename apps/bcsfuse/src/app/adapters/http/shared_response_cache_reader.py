"""Bounded reader for the cross-pod L2 response cache."""

from __future__ import annotations

import asyncio
from concurrent.futures import Future as ConcurrentFuture
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from app.infra.public.observability.shared_response_cache_metrics import (
    record_shared_response_cache_error,
    record_shared_response_cache_hit,
    record_shared_response_cache_miss,
)

DEFAULT_L2_READ_CONCURRENCY = 4
DEFAULT_L2_READ_TIMEOUT_SECONDS = 0.05


class BoundedSharedResponseCacheReader:
    """Bound optional L2 reads independently from the request path.

    The process-wide semaphore is not released when an L2 lookup times out
    while its backend call is still running. The background completion task
    releases it only after the bounded executor worker finishes, so a slow
    cache backend cannot turn a burst of unique misses into an unbounded
    thread or MySQL-connection stampede.
    """

    def __init__(
        self,
        *,
        shared_response_cache: Any,
        timeout_seconds: float = DEFAULT_L2_READ_TIMEOUT_SECONDS,
        max_concurrent_reads: int = DEFAULT_L2_READ_CONCURRENCY,
    ) -> None:
        if shared_response_cache is None:
            raise ValueError("shared_response_cache is required")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if max_concurrent_reads <= 0:
            raise ValueError("max_concurrent_reads must be positive")

        self._shared_response_cache = shared_response_cache
        self._timeout_seconds = timeout_seconds
        self._semaphore = asyncio.Semaphore(max_concurrent_reads)
        self._executor = ThreadPoolExecutor(
            max_workers=max_concurrent_reads,
            thread_name_prefix="bcsfuse-l2-read",
        )
        self._release_tasks: set[asyncio.Task[None]] = set()

    async def get(
        self, key: str, *, index_version: int, operation: str
    ) -> bytes | None:
        """Read L2 as bounded optional work, never as a request dependency."""

        await self._semaphore.acquire()
        future = self._executor.submit(
            self._backend_get, key, index_version=index_version
        )
        try:
            payload = await asyncio.wait_for(
                asyncio.wrap_future(future),
                timeout=self._timeout_seconds,
            )
        except asyncio.TimeoutError:
            self._release_when_complete(future)
            record_shared_response_cache_error(operation, stage="read")
            return None
        except asyncio.CancelledError:
            self._release_when_complete(future)
            raise
        except Exception:
            self._semaphore.release()
            record_shared_response_cache_error(operation, stage="read")
            return None
        finally:
            self._discard_finished_release_tasks()

        self._semaphore.release()
        if not isinstance(payload, bytes):
            record_shared_response_cache_miss(operation, stage="read")
            return None
        record_shared_response_cache_hit(operation, stage="read")
        return payload

    async def close(self) -> None:
        """Wait for detached backend calls, then stop the bounded reader."""

        for task in tuple(self._release_tasks):
            if not task.done():
                await asyncio.gather(task, return_exceptions=True)
        self._executor.shutdown(wait=True, cancel_futures=True)
        self._release_tasks.clear()

    def _backend_get(self, key: str, *, index_version: int) -> bytes | None:
        return self._shared_response_cache.get(key, index_version=index_version)

    def _release_when_complete(self, future: ConcurrentFuture[bytes | None]) -> None:
        task = asyncio.create_task(self._release_after_future(future))
        self._release_tasks.add(task)
        task.add_done_callback(self._release_tasks.discard)

    async def _release_after_future(
        self, future: ConcurrentFuture[bytes | None]
    ) -> None:
        try:
            await asyncio.wrap_future(future)
        except (Exception, asyncio.CancelledError):
            # A timed-out optional cache read is observed as an error metric;
            # the release task exists only to return the concurrency permit.
            pass
        finally:
            self._semaphore.release()

    def _discard_finished_release_tasks(self) -> None:
        self._release_tasks = {
            task for task in self._release_tasks if not task.done()
        }


__all__ = [
    "BoundedSharedResponseCacheReader",
    "DEFAULT_L2_READ_CONCURRENCY",
]
