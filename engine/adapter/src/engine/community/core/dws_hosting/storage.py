"""Do not release runtime ownership while a cancelled SQLite call still runs."""

import asyncio


async def durable_call(function, /, *args, **kwargs):
    task = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        # SQLite threads cannot be cancelled; join before releasing process ownership.
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
        task.result()
        raise
