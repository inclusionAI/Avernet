"""Application embedding execution without coupling transport to providers."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable
from typing import Any

Offload = Callable[[Callable[[], Any]], Awaitable[Any]]


async def execute_embedding_query(
    provider: Any,
    query: str,
    timeout_seconds: float | None,
    offload: Offload,
) -> list[float]:
    """Embed one online query on the most appropriate execution path.

    Native async providers are awaited directly and never consume a host worker.
    The legacy synchronous provider keeps the existing bounded offload path so
    indexing and un-migrated compositions remain unchanged.
    """

    if provider is None:
        return []
    embed = getattr(provider, "embed", None)
    if callable(embed) and inspect.iscoroutinefunction(embed):
        if timeout_seconds is None:
            return await embed(query)
        return await asyncio.wait_for(embed(query), timeout=timeout_seconds)
    if timeout_seconds is None:
        return await offload(lambda: provider.embed(query))
    return await asyncio.wait_for(
        offload(lambda: provider.embed(query)),
        timeout=timeout_seconds,
    )


__all__ = ["execute_embedding_query"]
