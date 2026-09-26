"""进程内事件总线，支持同步分发 + 异步处理。"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, Coroutine

from pydantic import BaseModel

logger = logging.getLogger(__name__)

Handler = Callable[[BaseModel], Coroutine[Any, Any, None]]


class EventBus:
    """内存事件总线：同步分发，handler 异步执行。"""

    def __init__(self) -> None:
        self._handlers: dict[type[BaseModel], list[Handler]] = {}

    def subscribe(self, event_type: type[BaseModel], handler: Handler) -> None:
        self._handlers.setdefault(event_type, []).append(handler)

    def publish(self, event: BaseModel) -> None:
        handlers = self._handlers.get(type(event), [])
        for handler in handlers:
            try:
                asyncio.create_task(handler(event))
            except RuntimeError:
                logger.warning("No running event loop, skipping event %s", type(event).__name__)


_event_bus: EventBus | None = None


def get_event_bus() -> EventBus:
    global _event_bus
    if _event_bus is None:
        _event_bus = EventBus()
    return _event_bus


def reset_event_bus() -> None:
    global _event_bus
    _event_bus = None