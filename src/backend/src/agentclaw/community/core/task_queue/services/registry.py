"""Handler registry — maps a ``task_type`` to the code that runs it.

A handler is anything satisfying :class:`TaskHandler`: a ``task_type`` it
serves plus a ``handle(payload)`` that returns a :class:`TaskOutcome`. The
worker looks up the handler for each claimed task's ``task_type`` and runs it.

**Registration contract (relies on the two-phase lifecycle).** Adopters
register their handlers in their own ``Lifecycle.bootstrap()``; the
``TaskWorker`` starts its claim loop in ``Lifecycle.startup()``. The app's
lifespan runner completes *every* ``bootstrap()`` before *any* ``startup()``
(``adapters/http/app.py`` + ``kernel/lifecycle.py``), so the registry is
guaranteed fully populated before the worker claims its first task — no
separate ordering mechanism is needed. The registry itself is a DI singleton,
so every participant sees the same instance.
"""
from __future__ import annotations

from typing import Dict, Optional, Protocol, runtime_checkable

from agentclaw.community.core.task_queue.types import TaskOutcome


@runtime_checkable
class TaskHandler(Protocol):
    """Runs one ``task_type``. Implementations are plain objects (no base
    class required) — registered into a :class:`HandlerRegistry`."""

    @property
    def task_type(self) -> str:
        """The task type this handler serves (the registry key)."""
        ...

    def handle(self, payload: Optional[dict]) -> TaskOutcome:
        """Execute the work for one task and return what to do next.

        Called by the worker off the event loop (via ``asyncio.to_thread``),
        so a synchronous body is fine. Returning is the normal path; raising
        is treated by the worker as an implicit :class:`Retry` (with backoff).
        Side effects must be safe to re-run — the queue guarantees
        single-claim execution but at-least-once invocation across crashes.
        """
        ...


class HandlerRegistry:
    """In-memory ``task_type`` → :class:`TaskHandler` map (a DI singleton)."""

    def __init__(self) -> None:
        self._handlers: Dict[str, TaskHandler] = {}

    def register(self, handler: TaskHandler) -> None:
        """Register ``handler`` under its ``task_type``.

        Raises :class:`ValueError` on a duplicate ``task_type`` — two handlers
        for the same type is a wiring bug, not a silent last-wins.
        """
        task_type = handler.task_type
        if task_type in self._handlers:
            raise ValueError(
                f"task_type {task_type!r} already has a handler registered "
                f"({type(self._handlers[task_type]).__name__})"
            )
        self._handlers[task_type] = handler

    def get(self, task_type: str) -> Optional[TaskHandler]:
        """Return the handler for ``task_type``, or ``None`` if unregistered."""
        return self._handlers.get(task_type)
