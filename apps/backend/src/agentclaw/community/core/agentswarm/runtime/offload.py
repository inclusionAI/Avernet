"""Slice-path DB offload seam (task 2.1, agentswarm-async-run-and-resume)."""
from __future__ import annotations

import asyncio

# Hard cap of in-flight offloads per worker loop; deliberately not a config
# knob — these are short, single-row commits.
_MAX_INFLIGHT_OFFLOADS = 8

# Created eagerly, not lazily: a module-level lazy double-checked singleton is
# a forbidden pattern here (tests/community/architecture/test_no_dcl_singletons),
# and since Python 3.10 the primitive binds its loop on first use, never at
# construction — so eager creation is safe across per-test asyncio.run loops
# as long as no waiter outlives its loop (the pool drains every offload).
_SEMAPHORE = asyncio.Semaphore(_MAX_INFLIGHT_OFFLOADS)


async def run_blocking(func, *args, **kwargs):
    """Keep DB I/O off the worker loop so heartbeats/drain signals never stall."""
    async with _SEMAPHORE:
        return await asyncio.to_thread(func, *args, **kwargs)
