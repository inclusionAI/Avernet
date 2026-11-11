"""Intersect BCS discovery order with Backend service-runtime availability."""

from collections.abc import Callable
from typing import Any


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
    batch_size = 100
    start = (page - 1) * page_size
    items: list[dict[str, Any]] = []
    total = 0
    batch = 1
    while True:
        result = read_page(page=batch, page_size=batch_size, **kwargs)
        rows = result["items"]
        for bot in rows:
            if bot.get("bot_type") == "service" and bot.get("online_available") is not True:
                continue
            if start <= total < start + page_size:
                items.append(bot)
            total += 1
        if batch * batch_size >= result["total"] or not rows:
            break
        batch += 1
    return {"total": total, "items": items}
