"""Local, in-memory Todo collaboration double — no B service is reached."""

from __future__ import annotations

import itertools
from typing import Iterator

from agentclaw.community.plugin_api.impl_registry import Flavor, Mode, plugin_impl
from agentclaw.community.plugin_api.todo_collaboration import (
    TodoCollaborationContext,
    TodoCollaborationContextRequest,
    TodoCollaborationEvent,
    TodoCollaborationEventRequest,
    TodoCollaborationPlugin,
)


@plugin_impl(
    mode=Mode.LOCAL,
    flavor=Flavor.SIMULATOR,
    rationale="simulates one B Context with an append-only event list",
)
class LocalTodoCollaborationPlugin(TodoCollaborationPlugin):
    """Deterministic event simulator for tests and offline development."""

    def __init__(self) -> None:
        self._contexts: dict[str, list[TodoCollaborationEvent]] = {}
        self._seq = itertools.count(1)

    def reset(self) -> None:
        self._contexts.clear()
        self._seq = itertools.count(1)

    def create_context(
        self, request: TodoCollaborationContextRequest
    ) -> TodoCollaborationContext:
        collab_context_id = f"context_{request.task_id}"
        context = TodoCollaborationContext(collab_context_id=collab_context_id)
        if collab_context_id in self._contexts:
            return context
        self._contexts[collab_context_id] = list(self._initial_events(request))
        return context

    def append_event(
        self, *, collab_context_id: str, request: TodoCollaborationEventRequest
    ) -> TodoCollaborationEvent:
        events = self._require_context(collab_context_id)
        event = TodoCollaborationEvent(
            event_id=f"event_{next(self._seq)}",
            event_seq=len(events) + 1,
            event_type=request.event_type,
            actor_id=request.actor_id,
            actor_type="human",
            content=request.content,
            execution_id=f"execution_{collab_context_id}",
            context_version=f"v{len(events) + 1}",
            status="in_progress",
            artifacts=(),
            created_at="2026-11-10T00:00:00Z",
        )
        events.append(event)
        return event

    def list_events(
        self, *, collab_context_id: str
    ) -> tuple[TodoCollaborationEvent, ...]:
        return tuple(self._require_context(collab_context_id))

    def _require_context(self, collab_context_id: str) -> list[TodoCollaborationEvent]:
        events = self._contexts.get(collab_context_id)
        if events is None:
            raise ValueError(f"collab context {collab_context_id} not found")
        return events

    def _initial_events(
        self, request: TodoCollaborationContextRequest
    ) -> Iterator[TodoCollaborationEvent]:
        first = TodoCollaborationEvent(
            event_id="event_1",
            event_seq=1,
            event_type="task.created",
            actor_id=request.owner_id,
            actor_type="human",
            content=request.collaboration_goal,
            execution_id=f"execution_context_{request.task_id}",
            context_version="v1",
            status="in_progress",
            artifacts=(),
            created_at="2026-11-10T00:00:00Z",
        )
        second = TodoCollaborationEvent(
            event_id="event_2",
            event_seq=2,
            event_type="execution.status",
            actor_id=request.bot_id,
            actor_type="bot",
            content="single bot started",
            execution_id=f"execution_context_{request.task_id}",
            context_version="v1",
            status="in_progress",
            artifacts=(),
            created_at="2026-11-10T00:01:00Z",
        )
        yield first
        yield second
