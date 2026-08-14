"""SSE heartbeat wrapper and chunk-to-SSE conversion helper.

Wraps a serialized SSE string stream so that when no bytes are yielded
for ``interval`` seconds, a ``": heartbeat\\n\\n"`` comment frame is emitted.
This prevents downstream idle timeouts even when the upstream is active
but the converter filters out all chunks (e.g. high-frequency tool
update/progress events).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass

from ._models import StreamChunk
from ._protocol import StreamConverter

HEARTBEAT_SSE = ": heartbeat\n\n"


@dataclass
class _End:
    """Sentinel marking stream completion."""


async def convert_chunks_to_sse(
    chunk_iter: AsyncIterator[StreamChunk],
    converter: StreamConverter,
    run_id: str,
    *,
    prefix: list[str] | None = None,
    on_error: Callable[[Exception], str] | None = None,
) -> AsyncIterator[str]:
    """Convert StreamChunks to SSE strings via a converter.

    Yields optional *prefix* SSE strings first (e.g. a ready event),
    then converts each chunk. Chunks that convert to None are skipped.
    On exception, calls *on_error* if provided; otherwise re-raises.
    """
    if prefix:
        for sse in prefix:
            yield sse
    try:
        async for chunk in chunk_iter:
            event = converter.convert(chunk, run_id=run_id)
            if event is None:
                continue
            yield event.to_sse()
    except Exception as e:
        if on_error is not None:
            yield on_error(e)
        else:
            raise


async def with_sse_heartbeat(
    sse_iter: AsyncIterator[str],
    *,
    interval: float = 10.0,
) -> AsyncIterator[str]:
    """Wrap an SSE string stream, injecting heartbeat when idle.

    Uses a producer-consumer pattern: a background task drains *sse_iter*
    into a queue; the main loop reads from the queue with a timeout. On
    timeout it yields :data:`HEARTBEAT_SSE`. Cancelling the queue.get()
    timeout does **not** cancel the original generator.
    """
    queue: asyncio.Queue[str | _End | Exception] = asyncio.Queue()
    end = _End()

    async def producer() -> None:
        try:
            async for sse in sse_iter:
                await queue.put(sse)
        except Exception as e:
            await queue.put(e)
        finally:
            await queue.put(end)

    producer_task = asyncio.create_task(producer())
    try:
        while True:
            try:
                item: str | _End | Exception = await asyncio.wait_for(
                    queue.get(), timeout=interval
                )
            except TimeoutError:
                yield HEARTBEAT_SSE
                continue
            if item is end:
                return
            if isinstance(item, Exception):
                raise item
            yield item
    finally:
        producer_task.cancel()
        try:
            await producer_task
        except (asyncio.CancelledError, Exception):
            pass
