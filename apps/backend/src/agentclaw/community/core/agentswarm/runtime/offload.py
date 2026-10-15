"""Slice-path DB offload seam (task 2.1, agentswarm-async-run-and-resume)."""
from __future__ import annotations

import asyncio

# Hard cap of in-flight offloads per worker loop; deliberately not a config
# knob — these are short, single-row commits.
_MAX_INFLIGHT_OFFLOADS = 8

# Lazy so the semaphore is created and loop-bound on first use, not at import.
_SEMAPHORE: asyncio.Semaphore | None = None


def _semaphore() -> asyncio.Semaphore:
    global _SEMAPHORE
    if _SEMAPHORE is None:
        _SEMAPHORE = asyncio.Semaphore(_MAX_INFLIGHT_OFFLOADS)
    return _SEMAPHORE


async def run_blocking(func, *args, **kwargs):
    """Keep DB I/O off the worker loop so heartbeats/drain signals never stall."""
    async with _semaphore():
        return await asyncio.to_thread(func, *args, **kwargs)