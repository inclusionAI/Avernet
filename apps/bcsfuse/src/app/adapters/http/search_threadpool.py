"""Bounded worker capacity dedicated to search and recommendation delivery."""

from __future__ import annotations

import functools
from collections.abc import Awaitable, Callable
from typing import Any

from anyio import to_thread

from app.application.services.retrieval_dependency_circuit import (
    RetrievalDependencyCircuit,
)

_search_thread_limiter: to_thread.CapacityLimiter = to_thread.CapacityLimiter(64)
_local_fallback_thread_limiter: to_thread.CapacityLimiter = (
    to_thread.CapacityLimiter(64)
)


def configure_search_thread_capacity(max_workers: int) -> None:
    """Align the dedicated search worker pool with process admission."""
    global _search_thread_limiter
    if max_workers <= 0:
        raise ValueError("search thread capacity must be positive")
    _search_thread_limiter = to_thread.CapacityLimiter(max_workers)


def configure_local_fallback_thread_capacity(max_workers: int) -> None:
    """Align a separate worker budget with terminal-fallback admission."""
    global _local_fallback_thread_limiter
    if max_workers <= 0:
        raise ValueError("local fallback thread capacity must be positive")
    _local_fallback_thread_limiter = to_thread.CapacityLimiter(max_workers)


def get_search_thread_capacity() -> int:
    """Return the configured dedicated search worker capacity."""
    return int(_search_thread_limiter.total_tokens)


def get_local_fallback_thread_capacity() -> int:
    """Return the terminal-fallback worker capacity."""
    return int(_local_fallback_thread_limiter.total_tokens)


def run_search_in_threadpool(func: Any, /, *args: Any, **kwargs: Any) -> Any:
    """Run admitted search work on its own bounded pool.

    This coroutine deliberately returns an awaitable to match
    Starlette's run_in_threadpool. It keeps admission, rejected with 429 by
    SearchCapacityMiddleware, aligned with the available search workers.
    """
    call = functools.partial(func, *args, **kwargs)
    return to_thread.run_sync(call, limiter=_search_thread_limiter)


def run_local_fallback_in_threadpool(
    func: Any, /, *args: Any, **kwargs: Any
) -> Any:
    """Run terminal local snapshot work without consuming search workers."""
    call = functools.partial(func, *args, **kwargs)
    return to_thread.run_sync(call, limiter=_local_fallback_thread_limiter)


def route_search_offload(
    circuit: RetrievalDependencyCircuit | None,
) -> Callable[[Any], Awaitable[Any]]:
    """Choose the worker pool from the current terminal outage state."""

    async def offload(operation: Any, /) -> Any:
        if circuit is not None and circuit.outaged:
            return await run_local_fallback_in_threadpool(operation)
        return await run_search_in_threadpool(operation)

    return offload


__all__ = [
    "configure_local_fallback_thread_capacity",
    "configure_search_thread_capacity",
    "get_local_fallback_thread_capacity",
    "get_search_thread_capacity",
    "route_search_offload",
    "run_local_fallback_in_threadpool",
    "run_search_in_threadpool",
]
