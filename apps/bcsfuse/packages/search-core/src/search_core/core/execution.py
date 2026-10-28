"""Bounded synchronous dependency execution with deadlines and a small circuit."""

from __future__ import annotations

import asyncio
import math
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Lock

from search_core.api.errors import SearchOverloaded, SearchUnavailable


class ExecutionPool:
    """No queued work. A timed-out call keeps its permit until it physically ends.

    Providers MUST additionally configure finite network timeouts. Python cannot
    stop a running sync call; the bound prevents those calls multiplying.
    """

    def __init__(
        self,
        *,
        workers: int,
        timeout_seconds: float,
        failure_threshold: int = 3,
        cooldown_seconds: float = 5.0,
    ):
        if workers < 1 or failure_threshold < 1:
            raise ValueError("positive workers and failure threshold required")
        if any(
            not math.isfinite(v) or v <= 0 for v in (timeout_seconds, cooldown_seconds)
        ):
            raise ValueError("finite positive timeout and cooldown required")
        self._executor = ThreadPoolExecutor(
            max_workers=workers, thread_name_prefix="search-call"
        )
        self.workers, self.timeout = workers, timeout_seconds
        self.threshold, self.cooldown = failure_threshold, cooldown_seconds
        self._lock = Lock()
        self._inflight = self._failures = 0
        self._open_until = 0.0
        self._closed = False

    @property
    def inflight(self):
        with self._lock:
            return self._inflight

    def _finished(self, future):
        with self._lock:
            self._inflight -= 1

    async def call(self, function, *args):
        with self._lock:
            if self._closed or time.monotonic() < self._open_until:
                raise SearchUnavailable("dependency circuit is open")
            if self._inflight >= self.workers:
                raise SearchOverloaded("dependency execution capacity occupied")
            self._inflight += 1
        try:
            future = self._executor.submit(function, *args)
        except BaseException:
            with self._lock:
                self._inflight -= 1
            raise
        future.add_done_callback(self._finished)
        wrapped = asyncio.wrap_future(future)
        # Consume late exceptions after timeout/cancellation without releasing a
        # physical permit early or leaving an unobserved asyncio Future error.
        wrapped.add_done_callback(lambda f: None if f.cancelled() else f.exception())
        try:
            result = await asyncio.wait_for(asyncio.shield(wrapped), self.timeout)
        except asyncio.CancelledError:
            raise
        except Exception:
            with self._lock:
                self._failures += 1
                if self._failures >= self.threshold:
                    self._open_until = time.monotonic() + self.cooldown
            raise
        with self._lock:
            self._failures = 0
        return result

    def close(self):
        with self._lock:
            self._closed = True
        self._executor.shutdown(wait=True, cancel_futures=True)
