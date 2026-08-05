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


class RequiredEventDeliveryError(RuntimeError):
    """A required event handler failed.

    Required handlers are used only at durable hand-off boundaries.  The
    publisher must surface this error so its own retry mechanism can redeliver
    the lifecycle signal.
    """


class EventBus:
    """In-process synchronous event bus."""

    def __init__(self) -> None:
        self._handlers: dict[Type[Any], list[EventHandler]] = defaultdict(list)
        self._required_handlers: set[tuple[Type[Any], EventHandler]] = set()
        self._lock = RLock()

    def subscribe(
        self,
        event_type: Type[Any],
        handler: EventHandler,
        *,
        required: bool = False,
    ) -> None:
        with self._lock:
            self._handlers[event_type].append(handler)
            if required:
                self._required_handlers.add((event_type, handler))
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
            required_handlers = {
                handler
                for registered_type, handler in self._required_handlers
                if registered_type is event_type
            }
        logger.info(
            "[EventBus.publish] event_type=%s handlers=%d",
            event_type.__name__,
            len(handlers),
        )
        required_failure: Exception | None = None
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
                if handler in required_handlers and required_failure is None:
                    required_failure = RequiredEventDeliveryError(
                        f"required handler failed for {event_type.__name__}: "
                        f"{handler_name}"
                    )
        if required_failure is not None:
            raise required_failure

    def clear(self) -> None:
        with self._lock:
            self._handlers.clear()
            self._required_handlers.clear()


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
