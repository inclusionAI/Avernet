"""Opt-in, bounded milestone timeline for slow runtime reads.

Times are offsets from the access middleware's monotonic start, not additive
durations. Only pass static operation names; never attach request data.
"""

from __future__ import annotations

import asyncio
from contextvars import ContextVar
from dataclasses import dataclass, field
from time import perf_counter


@dataclass
class Timeline:
    started: float
    points: list[str] = field(default_factory=list)


# to_thread copies the context, so all serial hops append to one request.
current: ContextVar[Timeline | None] = ContextVar("runtime_list_timeline", default=None)


def mark(name: str) -> None:
    timeline = current.get()
    if timeline is not None and len(timeline.points) < 256:
        elapsed = (perf_counter() - timeline.started) * 1000
        timeline.points.append(f"{name}:{elapsed:.1f}")


async def timed_thread(label, fn, /, *args, **kwargs):
    if current.get() is None:
        return await asyncio.to_thread(fn, *args, **kwargs)

    def run():
        mark(label + ".start")
        try:
            return fn(*args, **kwargs)
        finally:
            mark(label + ".done")

    mark(label + ".submit")
    try:
        return await asyncio.to_thread(run)
    finally:
        mark(label + ".resume")
