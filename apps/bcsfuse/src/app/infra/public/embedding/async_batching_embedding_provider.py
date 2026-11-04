"""Native-async concurrent batching for online query embeddings."""

from __future__ import annotations

import asyncio
from typing import Any

from app.infra.public.embedding.cached_embedding_provider import (
    _get_embedding_cache_metrics,
    normalize_embedding_query,
)


class AsyncBatchingEmbeddingProvider:
    """Coalesce concurrent unique queries without consuming worker threads.

    A single event-loop dispatcher forms bounded batches. It immediately
    schedules up to ``max_workers`` native ``embed_batch`` calls; cache-miss
    requests wait on per-query futures rather than a host worker thread. One
    batch therefore consumes one shared model-call slot.
    """

    def __init__(
        self,
        backend: Any,
        *,
        max_batch_size: int = 16,
        batch_wait_seconds: float = 0.05,
        max_workers: int = 4,
    ) -> None:
        if max_batch_size <= 0:
            raise ValueError("embedding max_batch_size must be positive")
        if batch_wait_seconds < 0:
            raise ValueError("embedding batch_wait_seconds cannot be negative")
        if max_workers <= 0:
            raise ValueError("embedding batch max_workers must be positive")

        self.backend = backend
        self.max_batch_size = max_batch_size
        self.batch_wait_seconds = batch_wait_seconds
        self.max_workers = max_workers
        self.model = str(getattr(backend, "model", "unknown"))
        self._metrics = _get_embedding_cache_metrics()
        self._lock = asyncio.Lock()
        self._pending: dict[str, asyncio.Future[list[float]]] = {}
        self._dispatcher: asyncio.Task[None] | None = None
        self._active_batches: set[asyncio.Task[None]] = set()
        self._backend_batch_calls = 0
        self._batched_queries = 0
        self._closed = False

    @property
    def dimension(self) -> int:
        return int(self.backend.dimension)

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def pending_queries(self) -> int:
        return len(self._pending)

    @property
    def stats(self) -> dict[str, float | int]:
        return {
            "backend_batch_calls": self._backend_batch_calls,
            "batched_queries": self._batched_queries,
            "pending_queries": len(self._pending),
            "active_batch_workers": len(self._active_batches),
            "max_batch_size": self.max_batch_size,
            "max_workers": self.max_workers,
        }

    async def embed(self, text: str) -> list[float]:
        if self._closed:
            raise RuntimeError("embedding batching provider is closed")

        key = normalize_embedding_query(text)
        async with self._lock:
            if self._closed:
                raise RuntimeError("embedding batching provider is closed")
            existing = self._pending.get(key)
            if existing is not None and not existing.done():
                future = existing
            else:
                future = asyncio.get_running_loop().create_future()
                self._pending[key] = future

            self._ensure_dispatcher_locked()
        return list(await asyncio.shield(future))

    def _ensure_dispatcher_locked(self) -> None:
        if self._dispatcher is not None and not self._dispatcher.done():
            return
        self._dispatcher = asyncio.create_task(self._dispatch())

    async def _dispatch(self) -> None:
        try:
            loop = asyncio.get_running_loop()
            await asyncio.sleep(self.batch_wait_seconds)
            partial_idle_at: float | None = None
            while True:
                waiting_tasks: list[asyncio.Task[None]] = []
                hold_tail = False
                async with self._lock:
                    if self._closed:
                        return
                    if not self._pending and not self._active_batches:
                        self._dispatcher = None
                        return

                    if self._active_batches:
                        partial_idle_at = None

                    if len(self._active_batches) < self.max_workers:
                        keys = list(self._pending)[: self.max_batch_size]
                        # A bounded HTTP admission semaphore only releases
                        # the next request wave after an active batch returns.
                        # Launching an incomplete tail immediately amplifies
                        # model calls and exhausts the shared budget. Hold it
                        # for a short arrival window after workers become idle.
                        if keys and len(keys) == self.max_batch_size:
                            selected = [
                                (key, self._pending.pop(key))
                                for key in keys
                                if key in self._pending
                            ]
                            task = asyncio.create_task(
                                self._process_batch(selected)
                            )
                            self._active_batches.add(task)
                            task.add_done_callback(
                                self._active_batches.discard
                            )
                            continue
                        if keys and not self._active_batches:
                            now = loop.time()
                            if partial_idle_at is None:
                                partial_idle_at = now
                            if now - partial_idle_at < self.batch_wait_seconds:
                                hold_tail = True
                            else:
                                selected = [
                                    (key, self._pending.pop(key))
                                    for key in keys
                                    if key in self._pending
                                ]
                                task = asyncio.create_task(
                                    self._process_batch(selected)
                                )
                                self._active_batches.add(task)
                                task.add_done_callback(
                                    self._active_batches.discard
                                )
                                continue

                    waiting_tasks = list(self._active_batches)

                if hold_tail:
                    await asyncio.sleep(
                        max(0.001, partial_idle_at + self.batch_wait_seconds - loop.time())
                        if partial_idle_at is not None
                        else 0.001
                    )
                    continue
                if not waiting_tasks:
                    await asyncio.sleep(0.001)
                    continue

                done, _ = await asyncio.wait(
                    waiting_tasks, return_when=asyncio.FIRST_COMPLETED
                )
                for task in done:
                    if task.done() and task.exception() is not None:
                        task.exception()
        except asyncio.CancelledError:
            raise
        finally:
            if self._dispatcher is not None and self._dispatcher.done():
                self._dispatcher = None

    async def _process_batch(
        self, selected: list[tuple[str, asyncio.Future[list[float]]]]
    ) -> None:
        texts = [key for key, _ in selected]
        futures = [future for _, future in selected]
        self._backend_batch_calls += 1
        self._batched_queries += len(texts)
        self._metrics["batch_calls"].labels(self.model).inc()
        self._metrics["batched_queries"].labels(self.model).inc(len(texts))
        self._metrics["batch_size"].labels(self.model).observe(float(len(texts)))

        try:
            vectors = await self.backend.embed_batch(texts)
            if len(vectors) != len(texts):
                raise ValueError(
                    "embedding batch response count does not match the request"
                )
        except BaseException as error:
            for future in futures:
                if not future.done():
                    future.set_exception(error)
            return

        for vector, future in zip(vectors, futures, strict=True):
            if not future.done():
                future.set_result(list(vector))

    async def aclose(self) -> None:
        async with self._lock:
            self._closed = True
            pending = list(self._pending.values())
            self._pending.clear()
            for future in pending:
                if not future.done():
                    future.set_exception(
                        RuntimeError("embedding batching provider is closed")
                    )

            dispatcher = self._dispatcher
            active = list(self._active_batches)

        awaitables: list[Any] = []
        for task in active:
            if not task.done():
                task.cancel()
            awaitables.append(task)
        if dispatcher is not None and not dispatcher.done():
            dispatcher.cancel()
            awaitables.append(dispatcher)
        if awaitables:
            await asyncio.gather(*awaitables, return_exceptions=True)

        close = getattr(self.backend, "aclose", None)
        if close is not None:
            await close()
        self._closed = True


__all__ = ["AsyncBatchingEmbeddingProvider"]
