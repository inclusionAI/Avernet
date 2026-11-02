"""Bounded background writer for the cross-pod L2 response cache."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from app.infra.public.observability.shared_response_cache_metrics import (
    record_shared_response_cache_error,
    record_shared_response_cache_store,
)

DEFAULT_L2_WRITE_QUEUE_SIZE = 128
DEFAULT_L2_WRITE_WORKERS = 2
DEFAULT_L2_WRITE_TTL_SECONDS = 300.0
DEFAULT_L2_WRITE_TIMEOUT_SECONDS = 0.05


@dataclass(frozen=True)
class _WriteJob:
    key: str
    payload: bytes
    index_version: int


class BoundedSharedResponseCacheWriter:
    """Queue L2 writes without allowing them to grow or block request responses."""

    def __init__(
        self,
        *,
        shared_response_cache: Any,
        operation: str,
        ttl_seconds: float,
        timeout_seconds: float,
        queue_size: int = DEFAULT_L2_WRITE_QUEUE_SIZE,
        worker_count: int = DEFAULT_L2_WRITE_WORKERS,
    ) -> None:
        if shared_response_cache is None:
            raise ValueError("shared_response_cache is required")
        if queue_size <= 0:
            raise ValueError("queue_size must be positive")
        if worker_count <= 0:
            raise ValueError("worker_count must be positive")

        self._shared_response_cache = shared_response_cache
        self._operation = operation
        self._closed = False
        self._ttl_seconds = ttl_seconds
        self._timeout_seconds = timeout_seconds
        self._queue: asyncio.Queue[_WriteJob] = asyncio.Queue(maxsize=queue_size)
        self._worker_count = worker_count
        self._workers: set[asyncio.Task[None]] = set()

    @property
    def operation(self) -> str:
        return self._operation

    @property
    def is_closed(self) -> bool:
        return self._closed

    def submit(self, *, key: str, payload: bytes, index_version: int) -> bool:
        """Enqueue one write, never wait, and explicitly report a full queue."""

        if self._closed:
            record_shared_response_cache_error(self._operation, stage="enqueue")
            return False
        self._ensure_workers()
        try:
            self._queue.put_nowait(
                _WriteJob(key=key, payload=payload, index_version=index_version)
            )
        except asyncio.QueueFull:
            record_shared_response_cache_error(self._operation, stage="enqueue")
            return False
        return True

    async def wait_idle(self) -> None:
        await self._queue.join()

    async def close(self) -> None:
        """Drain accepted writes, stop workers, and make close idempotent."""

        if self._closed:
            return
        await self.wait_idle()
        self._closed = True
        for worker in tuple(self._workers):
            worker.cancel()
        await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers.clear()

    def _ensure_workers(self) -> None:
        active_workers = {worker for worker in self._workers if not worker.done()}
        self._workers.clear()
        self._workers.update(active_workers)
        while len(self._workers) < self._worker_count:
            worker = asyncio.create_task(self._run_worker())
            self._workers.add(worker)
            worker.add_done_callback(self._workers.discard)

    async def _run_worker(self) -> None:
        while True:
            job = await self._queue.get()
            try:
                stored = await asyncio.wait_for(
                    asyncio.to_thread(
                        self._shared_response_cache.store,
                        job.key,
                        job.payload,
                        index_version=job.index_version,
                        ttl_seconds=self._ttl_seconds,
                    ),
                    timeout=self._timeout_seconds,
                )
                if stored:
                    record_shared_response_cache_store(
                        self._operation, stage="store"
                    )
                else:
                    record_shared_response_cache_error(
                        self._operation, stage="store"
                    )
            except Exception:
                record_shared_response_cache_error(self._operation, stage="store")
            finally:
                self._queue.task_done()


__all__ = [
    "BoundedSharedResponseCacheWriter",
    "DEFAULT_L2_WRITE_QUEUE_SIZE",
    "DEFAULT_L2_WRITE_WORKERS",
    "DEFAULT_L2_WRITE_TTL_SECONDS",
    "DEFAULT_L2_WRITE_TIMEOUT_SECONDS",
]
