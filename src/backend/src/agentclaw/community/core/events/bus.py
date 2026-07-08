"""Synchronous in-process event bus.

Design choices:
- Serial dispatch on the caller's thread (handlers currently cheap).
- Per-handler try/except — one failure never blocks siblings.
- Handlers registered by concrete event type — no inheritance walking.
- Module-level singleton via get_event_bus(); reset_event_bus() for tests.
"""
from __future__ import annotations

from collections import defaultdict
from threading import RLock
from typing import Any, Callable, Type

from agentclaw.community.log import get_logger

logger = get_logger()

EventHandler = Callable[[Any], None]


class EventBus:
    """In-process synchronous event bus."""

    def __init__(self) -> None:
        self._handlers: dict[Type[Any], list[EventHandler]] = defaultdict(list)
        self._lock = RLock()

    def subscribe(self, event_type: Type[Any], handler: EventHandler) -> None:
        with self._lock:
            self._handlers[event_type].append(handler)
        logger.info(
            "[EventBus.subscribe] event_type=%s handler=%s total=%d",
            event_type.__name__,
            getattr(handler, "__name__", repr(handler)),
            len(self._handlers[event_type]),
        )

    def publish(self, event: Any) -> None:
        event_type = type(event)
        with self._lock:
            handlers = list(self._handlers.get(event_type, ()))
        logger.info(
            "[EventBus.publish] event_type=%s handlers=%d",
            event_type.__name__,
            len(handlers),
        )
        for handler in handlers:
            handler_name = getattr(handler, "__name__", repr(handler))
            try:
                handler(event)
            except Exception:
                logger.exception(
                    "[EventBus.publish] handler raised: event_type=%s handler=%s",
                    event_type.__name__,
                    handler_name,
                )

    def clear(self) -> None:
        with self._lock:
            self._handlers.clear()


_bus: EventBus | None = None
_singleton_lock = RLock()


def get_event_bus() -> EventBus:
    global _bus
    with _singleton_lock:
        if _bus is None:
            _bus = EventBus()
        return _bus


def reset_event_bus() -> None:
    """Reset the module-level singleton. For tests and explicit re-wiring."""
    global _bus
    with _singleton_lock:
        _bus = None
