"""Bounded phase execution; no queued tasks survive failure or interruption."""
from __future__ import annotations

from collections.abc import Callable, Sequence
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from typing import TypeVar

T = TypeVar('T')
R = TypeVar('R')


def run_parallel(items: Sequence[T], action: Callable[[T], R], workers: int,
                 cancel: Callable[[], None]) -> list[R]:
    if not items:
        return []
    executor = ThreadPoolExecutor(max_workers=min(workers, len(items)), thread_name_prefix='agency')
    remaining = iter(enumerate(items))
    pending = {}
    results = {}

    def submit_next() -> None:
        entry = next(remaining, None)
        if entry is not None:
            index, item = entry
            pending[executor.submit(action, item)] = index

    try:
        for _ in range(min(workers, len(items))):
            submit_next()
        while pending:
            done, _ = wait(pending, timeout=0.1, return_when=FIRST_COMPLETED)
            # Observe all completed failures before starting any further work.
            for future in done:
                results[pending.pop(future)] = future.result()
            for _ in done:
                submit_next()
        return [results[index] for index in range(len(items))]
    except BaseException:
        cancel()
        for future in pending:
            future.cancel()
        raise
    finally:
        executor.shutdown(wait=True, cancel_futures=True)
