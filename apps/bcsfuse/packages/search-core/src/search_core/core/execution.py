"""Deadlines for native async calls and host-owned synchronous workers."""

import asyncio
import inspect
import math
import time
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context

from search_core.core.observation import (
    events,
    failure_reason,
    remaining_timeout,
    upstream_status,
)


class DeadlineExecutor:
    """No local quota, retries or circuit; queued work expires with its caller.

    Native async providers run on the event loop and never enter the thread pool.
    The host shares a standard thread pool for synchronous providers and drains
    it before closing clients.
    Running synchronous calls cannot be forcibly stopped, so providers MUST also
    configure finite network timeouts.
    """

    def __init__(
        self, executor: ThreadPoolExecutor, *, timeout_seconds: float, name: str
    ):
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("finite positive timeout required")
        self._executor = executor
        self.timeout, self.name = timeout_seconds, name

    async def call(self, function, *args):
        started = time.monotonic()
        timeout = remaining_timeout(self.timeout)
        future = None
        try:
            if timeout <= 0:
                raise TimeoutError("request budget exhausted")
            if inspect.iscoroutinefunction(function):
                return await asyncio.wait_for(function(*args), timeout)
            future = self._executor.submit(copy_context().run, function, *args)
            # Cancellation propagates to queued work. A running SDK call must
            # terminate using its own network timeout; the host drains on exit.
            return await asyncio.wait_for(asyncio.wrap_future(future), timeout)
        except asyncio.CancelledError:
            if future is not None:
                future.cancel()
            raise
        except Exception as error:
            if future is not None:
                future.cancel()
            events.record(
                "search_stage_failure",
                self.name,
                "budget_exhausted" if timeout <= 0 else failure_reason(error),
                elapsed_ms=round((time.monotonic() - started) * 1000, 2),
                timeout_ms=round(timeout * 1000, 2),
                error_type=type(error).__name__,
                upstream_status=upstream_status(error),
            )
            raise
