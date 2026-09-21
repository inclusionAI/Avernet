"""Durable semantic-event dispatcher.

The dispatcher is deliberately transport-agnostic. TaskGraphService owns the
pending event records; handlers own Planner/Dispatcher/Runner calls and must
write results back through Graph.report.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from agentclaw.community.core.task.task_runner.execution_events import (
    TaskSemanticEvent,
    TaskSemanticEventType,
)


EventHandler = Callable[[TaskSemanticEvent], Awaitable[Any]]


class TaskSemanticEventDispatcher:
    def __init__(self, graph: Any) -> None:
        self._graph = graph
        self._handlers: dict[TaskSemanticEventType, EventHandler] = {}

    def register(self, event_type: TaskSemanticEventType, handler: EventHandler) -> None:
        self._handlers[event_type] = handler

    async def dispatch_pending(self, task_id: str, *, limit: int = 100) -> int:
        delivered = 0
        # Drain events produced by handlers in the same logical turn.
        while delivered < limit:
            pending = self._graph.list_pending_semantic_events(
                task_id, limit=limit - delivered
            )
            if not pending:
                break
            progressed = 0
            for raw in pending:
                event = TaskSemanticEvent(
                    event_id=str(raw["event_id"]),
                    event_type=TaskSemanticEventType(str(raw["event_type"])),
                    task_id=str(raw["task_id"]),
                    node_id=raw.get("node_id"),
                    payload=dict(raw.get("payload") or {}),
                )
                handler = self._handlers.get(event.event_type)
                if handler is None:
                    continue
                await handler(event)
                self._graph.acknowledge_semantic_event(task_id, event.event_id)
                delivered += 1
                progressed += 1
            if not progressed:
                break
        return delivered
