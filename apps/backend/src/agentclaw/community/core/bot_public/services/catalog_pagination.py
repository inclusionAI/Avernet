"""Intersect BCS discovery order with Backend service-runtime availability."""

from collections import deque
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from contextvars import copy_context
from threading import BoundedSemaphore
from time import perf_counter
from typing import Any

from agentclaw.community.log import get_logger

logger = get_logger()
_BATCH_SIZE = 100
# At most four extra page workers per process. Busy scans fall back to serial;
# this is not a new request queue and does not change which callers can search.
_PARALLEL_SCANS = BoundedSemaphore(2)
# Include serial fallbacks and first pages in the upstream load budget too.
_PAGE_READS = BoundedSemaphore(4)


def _read_page(read_page: Callable[..., dict[str, Any]], **kwargs: Any) -> dict[str, Any]:
    with _PAGE_READS:
        return read_page(**kwargs)


def _catalog_pages(read_page: Callable[..., dict[str, Any]], **kwargs: Any) -> Iterator[dict[str, Any]]:
    batch = 1
    result = _read_page(read_page, page=batch, page_size=_BATCH_SIZE, **kwargs)
    yield result
    if batch * _BATCH_SIZE >= result["total"] or not result["items"]:
        return
    if not _PARALLEL_SCANS.acquire(blocking=False):
        while batch * _BATCH_SIZE < result["total"] and result["items"]:
            batch += 1
            result = _read_page(read_page, page=batch, page_size=_BATCH_SIZE, **kwargs)
            yield result
        return
    try:
        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="catalog-page") as executor:
            pending = deque()
            next_batch = 2
            try:
                while True:
                    # A separate Context is required for every concurrent worker.
                    while len(pending) < 2 and (next_batch - 1) * _BATCH_SIZE < result["total"]:
                        pending.append((next_batch, executor.submit(
                            copy_context().run, _read_page, read_page,
                            page=next_batch, page_size=_BATCH_SIZE, **kwargs,
                        )))
                        next_batch += 1
                    if not pending:
                        return
                    batch, future = pending.popleft()
                    result = future.result()  # Keep BCS page order, not completion order.
                    yield result
                    if batch * _BATCH_SIZE >= result["total"] or not result["items"]:
                        return
            finally:
                for _, future in pending:
                    future.cancel()
    finally:
        _PARALLEL_SCANS.release()


def online_catalog_page(
    read_page: Callable[..., dict[str, Any]],
    *,
    page: int,
    page_size: int,
    **kwargs: Any,
) -> dict[str, Any]:
    """Count the eligible intersection without loading the entire catalog.

    BCS owns visibility/order; Backend owns publication bindings. Scan bounded
    BCS pages, join each in one Backend query, and retain only the requested
    output window. Filtering a single BCS page cannot yield a correct total.
    """
    start = (page - 1) * page_size
    items: list[dict[str, Any]] = []
    total = 0
    batches = candidates = 0
    started = perf_counter()
    with closing(_catalog_pages(read_page, **kwargs)) as pages:
        for result in pages:
            batches += 1
            candidates += len(result["items"])
            for bot in result["items"]:
                if bot.get("bot_type") == "service" and bot.get("online_available") is not True:
                    continue
                if start <= total < start + page_size:
                    items.append(bot)
                total += 1
    logger.info(
        "event=catalog_scan.succeeded batches=%s candidates=%s eligible=%s returned=%s duration_ms=%.1f",
        batches, candidates, total, len(items), (perf_counter() - started) * 1000,
    )
    return {"total": total, "items": items}
