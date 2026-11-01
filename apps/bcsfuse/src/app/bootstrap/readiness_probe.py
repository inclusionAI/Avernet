"""Bounded readiness probes with singleflight and short-lived caching."""

from __future__ import annotations

import asyncio
import copy
import time
from collections.abc import Awaitable, Callable
from typing import Any

ReadinessResult = tuple[bool, dict[str, Any]]


class ReadinessProbeCache:
    """Keep /ready and /metrics responsive when a dependency probe stalls.

    The probe is always executed off the event loop. A timeout does not fail a
    recently healthy pod immediately; it first serves an explicit stale result.
    A blocking probe still runs as at most one per-process in-flight operation.
    """

    def __init__(
        self,
        *,
        max_probe_seconds: float = 1.0,
        cache_ttl_seconds: float = 0.50,
        stale_cache_max_age_seconds: float = 5.0,
    ) -> None:
        self.max_probe_seconds = max_probe_seconds
        self.cache_ttl_seconds = cache_ttl_seconds
        self.stale_cache_max_age_seconds = stale_cache_max_age_seconds
        self._lock = asyncio.Lock()
        self._probe_task: asyncio.Task[ReadinessResult] | None = None
        self._cached_ready: bool | None = None
        self._cached_info: dict[str, Any] = {}
        self._cached_at: float | None = None

    async def resolve(
        self,
        probe: Callable[[], Awaitable[ReadinessResult]],
    ) -> ReadinessResult:
        cached = self._fresh_cache(self.stale_cache_max_age_seconds)
        if cached is not None:
            is_ready, info = cached
            fresh_age = self._age(self.cache_ttl_seconds)
            if fresh_age is not None:
                return is_ready, self._annotated(
                    info,
                    cached=True,
                    stale=False,
                    age_seconds=fresh_age,
                )

        task = self._probe_task
        if task is not None and not task.done():
            return self._pending_result(cached)

        async with self._lock:
            cached = self._fresh_cache(self.cache_ttl_seconds)
            if cached is not None:
                is_ready, info = cached
                return is_ready, self._annotated(
                    info,
                    cached=True,
                    stale=False,
                    age_seconds=self._age(self.cache_ttl_seconds) or 0.0,
                )

            current_task = asyncio.create_task(probe())
            self._probe_task = current_task
            current_task.add_done_callback(self._watch_late_result)
            try:
                result = await asyncio.wait_for(
                    asyncio.shield(current_task),
                    timeout=self.max_probe_seconds,
                )
            except asyncio.TimeoutError:
                return self._pending_result(cached)
            self._record_result(result)
            return result[0], self._annotated(
                result[1],
                cached=False,
                stale=False,
                age_seconds=0.0,
            )

    def _pending_result(
        self,
        cached: ReadinessResult | None,
    ) -> ReadinessResult:
        if cached is not None:
            age = self._age(self.stale_cache_max_age_seconds) or 0.0
            is_ready, info = cached
            return is_ready, self._annotated(
                info,
                cached=True,
                stale=True,
                age_seconds=age,
                timeout=True,
            )
        return False, {
            "readiness_unknown": True,
            "readiness_probe_timeout": True,
            "readiness_probe_inflight": True,
        }

    def _record_result(self, result: ReadinessResult) -> None:
        self._cached_ready, self._cached_info = copy.deepcopy(result)
        self._cached_at = time.monotonic()

    def _record_task_result(self, task: asyncio.Task[Any] | None) -> None:
        if task is None or task.cancelled():
            return
        if task.exception() is not None:
            # Reading the exception here prevents a late completion from raising
            # an "exception was never retrieved" warning after a probe timeout.
            return

        if task is self._probe_task:
            self._cached_ready, self._cached_info = copy.deepcopy(task.result())
            self._cached_at = time.monotonic()

    def _fresh_cache(self, max_age_seconds: float) -> ReadinessResult | None:
        age = self._age(max_age_seconds)
        if age is None or self._cached_ready is None:
            return None
        return self._cached_ready, self._cached_info

    def _age(self, max_age_seconds: float) -> float | None:
        if self._cached_at is None:
            return None
        age = time.monotonic() - self._cached_at
        if age < 0 or age > max_age_seconds:
            return None
        return age

    @staticmethod
    def _annotated(
        info: dict[str, Any],
        *,
        cached: bool,
        stale: bool,
        age_seconds: float,
        timeout: bool = False,
    ) -> dict[str, Any]:
        result = copy.deepcopy(info)
        result["readiness_result_cached"] = cached
        result["readiness_result_stale"] = stale
        result["readiness_cache_age_seconds"] = round(age_seconds, 6)
        if timeout:
            result["readiness_probe_timeout"] = True
            result["readiness_probe_inflight"] = True
        return result

    def _watch_late_result(self, _: asyncio.Task[ReadinessResult]) -> None:
        self._record_task_result(self._probe_task)
