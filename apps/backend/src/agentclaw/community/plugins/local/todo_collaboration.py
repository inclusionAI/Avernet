"""Local in-memory implementation of Todo's collaboration Plugin API."""

from __future__ import annotations

import itertools
from dataclasses import dataclass, replace

from agentclaw.community.plugin_api.impl_registry import Flavor, Mode, plugin_impl
from agentclaw.community.plugin_api.todo_collaboration import (
    TodoCollaborationContext,
    TodoCollaborationContextRequest,
    TodoCollaborationError,
    TodoCollaborationEvent,
    TodoCollaborationEventRequest,
    TodoCollaborationPlugin,
)


@dataclass
class _LocalContext:
    request: TodoCollaborationContextRequest
    status: str
    version: int
    current_event_id: str | None
    events: list[TodoCollaborationEvent]


@plugin_impl(
    mode=Mode.LOCAL,
    flavor=Flavor.SIMULATOR,
    rationale="simulates B Context lifecycle and append-only events",
)
class LocalTodoCollaborationPlugin(TodoCollaborationPlugin):
    """Deterministic Context simulator for tests and offline development."""

    def __init__(self) -> None:
        self._contexts: dict[str, _LocalContext] = {}
        self._run_statuses: dict[str, str] = {}
        self._ids = itertools.count(1)

    def reset(self) -> None:
        self._contexts.clear()
        self._run_statuses.clear()
        self._ids = itertools.count(1)

    def create_context(
        self, request: TodoCollaborationContextRequest
    ) -> TodoCollaborationContext:
        context_id = f"context_{request.todo_id}"
        existing = self._contexts.get(context_id)
        if existing is not None:
            return self._snapshot(context_id, existing)
        event = self._event(
            event_seq=1,
            event_type="task.created",
            actor_id=request.principal_id,
            actor_type="human",
            content=request.objective,
            context_version=1,
            status="active",
        )
        local = _LocalContext(
            request=request,
            status="created",
            version=1,
            current_event_id=event.event_id,
            events=[event],
        )
        self._contexts[context_id] = local
        return self._snapshot(context_id, local)

    def get_context(
        self, *, collab_context_id: str, actor_id: str
    ) -> TodoCollaborationContext:
        return self._snapshot(
            collab_context_id, self._require_context(collab_context_id)
        )

    async def run_context(
        self, *, collab_context_id: str, actor_id: str
    ) -> TodoCollaborationContext:
        return self._run_context(collab_context_id)

    def start_context_for_test(
        self, *, collab_context_id: str
    ) -> TodoCollaborationContext:
        """Synchronously seed a started Context in endpoint fixtures."""
        return self._run_context(collab_context_id)

    def _run_context(self, collab_context_id: str) -> TodoCollaborationContext:
        local = self._require_context(collab_context_id)
        if local.status != "created":
            raise TodoCollaborationError("Context can run only from created")
        local.version += 1
        event = self._event(
            event_seq=len(local.events) + 1,
            event_type="task.started",
            actor_id=local.request.executor_id,
            actor_type="bot",
            content="",
            context_version=local.version,
            status="active",
            execution_id=f"run_{collab_context_id}",
        )
        local.events.append(event)
        local.current_event_id = event.event_id
        local.status = "in_progress"
        self._run_statuses[collab_context_id] = "RUNNING"
        return self._snapshot(collab_context_id, local)

    async def refresh_context_run(
        self, *, collab_context_id: str, actor_id: str
    ) -> TodoCollaborationContext:
        local = self._require_context(collab_context_id)
        if local.status == "done":
            return self._snapshot(collab_context_id, local)
        if local.status != "in_progress":
            raise TodoCollaborationError(
                "Context runtime can refresh only from in_progress"
            )
        if self._run_statuses.get(collab_context_id) == "COMPLETED":
            return self.complete_context(collab_context_id)
        return self._snapshot(collab_context_id, local)

    async def resume_context(
        self,
        *,
        collab_context_id: str,
        actor_id: str,
        triggered_by_event_id: str,
    ) -> TodoCollaborationContext:
        local = self._require_context(collab_context_id)
        if local.status != "done":
            raise TodoCollaborationError("Context can resume only from done")
        trigger = next(
            (
                event
                for event in local.events
                if event.event_id == triggered_by_event_id
            ),
            None,
        )
        if trigger is None or trigger.event_type != "user_interaction.posted":
            raise TodoCollaborationError("Context resume requires an interaction event")
        next_version = local.version + 1
        self.append_event(
            collab_context_id=collab_context_id,
            request=TodoCollaborationEventRequest(
                expected_version=local.version,
                event_kind="task.started",
                primitive_type="Task",
                actor_id=local.request.executor_id,
                actor_type="bot",
                parent_event=local.current_event_id,
                visibility="workspace",
                payload={
                    "run_id": f"run_{collab_context_id}_{next_version}",
                    "session_id": f"session_{collab_context_id}",
                    "resumed": True,
                    "triggered_by_event_id": triggered_by_event_id,
                },
            ),
        )
        resumed = self._require_context(collab_context_id)
        resumed.status = "in_progress"
        self._run_statuses[collab_context_id] = "RUNNING"
        return self._snapshot(collab_context_id, resumed)

    def set_run_status_for_test(self, *, collab_context_id: str, status: str) -> None:
        local = self._require_context(collab_context_id)
        if local.status != "in_progress":
            raise TodoCollaborationError(
                "Context runtime status can change only while in_progress"
            )
        self._run_statuses[collab_context_id] = status.strip().upper()

    def append_event(
        self, *, collab_context_id: str, request: TodoCollaborationEventRequest
    ) -> TodoCollaborationEvent:
        local = self._require_context(collab_context_id)
        if request.expected_version != local.version:
            raise TodoCollaborationError("Context version conflict")
        local.version += 1
        event = self._event(
            event_seq=len(local.events) + 1,
            event_type=request.event_kind,
            actor_id=request.actor_id,
            actor_type=request.actor_type,
            content=str(request.payload.get("content") or ""),
            context_version=local.version,
            status="active",
            execution_id=str(
                request.payload.get("run_id")
                or request.payload.get("execution_id")
                or ""
            ),
        )
        local.events.append(event)
        local.current_event_id = event.event_id
        return self._snapshot(collab_context_id, local).events[-1]

    def complete_context(self, collab_context_id: str) -> TodoCollaborationContext:
        """Test helper that simulates B accepting task.completed."""
        local = self._require_context(collab_context_id)
        if local.status != "in_progress":
            raise TodoCollaborationError("Context can complete only from in_progress")
        local.version += 1
        event = self._event(
            event_seq=len(local.events) + 1,
            event_type="task.completed",
            actor_id=local.request.executor_id,
            actor_type="bot",
            content="",
            context_version=local.version,
            status="active",
            execution_id=f"run_{collab_context_id}",
        )
        local.events.append(event)
        local.current_event_id = event.event_id
        local.status = "done"
        self._run_statuses[collab_context_id] = "COMPLETED"
        return self._snapshot(collab_context_id, local)

    def _require_context(self, collab_context_id: str) -> _LocalContext:
        context = self._contexts.get(collab_context_id)
        if context is None:
            raise TodoCollaborationError(
                f"collab context {collab_context_id} not found"
            )
        return context

    def _snapshot(
        self, collab_context_id: str, local: _LocalContext
    ) -> TodoCollaborationContext:
        events = tuple(
            replace(
                event,
                status=(
                    "active"
                    if event.event_id == local.current_event_id
                    else "completed"
                ),
            )
            for event in local.events
        )
        return TodoCollaborationContext(
            collab_context_id=collab_context_id,
            status=local.status,
            context_version=local.version,
            current_event_id=local.current_event_id,
            events=events,
        )

    def _event(
        self,
        *,
        event_seq: int,
        event_type: str,
        actor_id: str,
        actor_type: str,
        content: str,
        context_version: int,
        status: str,
        execution_id: str = "",
    ) -> TodoCollaborationEvent:
        return TodoCollaborationEvent(
            event_id=f"event_{next(self._ids)}",
            event_seq=event_seq,
            event_type=event_type,
            actor_id=actor_id,
            actor_type=actor_type,
            content=content,
            execution_id=execution_id,
            context_version=context_version,
            status=status,
            artifacts=(),
            created_at="2026-11-10T00:00:00Z",
        )
