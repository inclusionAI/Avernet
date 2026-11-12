"""In-process adapter tests for Todo's ContextCollab boundary."""

from __future__ import annotations

from typing import Any

import pytest

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabNotFoundError,
    ContextCollabParentEventError,
    ContextCollabValidationError,
    ContextCollabVersionConflict,
)
from agentclaw.community.core.task.context_collab.service_api import (
    ContextCollabCreatedResult,
    ContextCollabRunResultDTO,
    ContextCollabStatusProjection,
    EventAcceptedResult,
    TaskProgressProjectionDTO,
)
from agentclaw.community.plugin_api.context_collab_runtime import (
    ContextCollabRuntimeError,
)
from agentclaw.community.plugin_api.todo_collaboration import (
    TodoCollaborationContextRequest,
    TodoCollaborationError,
    TodoCollaborationEventRequest,
)
from agentclaw.community.plugins.community.todo_collaboration import (
    InProcessTodoCollaborationPlugin,
)

_CONTEXT_ID = "context_todo_1"


def _context_request() -> TodoCollaborationContextRequest:
    return TodoCollaborationContextRequest(
        space_id="7",
        environment="test",
        principal_id="user_1",
        product_kind="team_todo",
        workflow_kind="team_todo_execution",
        method_kind="Single",
        executor_id="bot_1",
        executor_owner_id="another_user",
        objective="Write the report",
        todo_id="todo_1",
    )


def _event_projection(
    *, event_id: str = "event_2", event_kind: str = "task.created"
) -> dict[str, Any]:
    return {
        "event_id": event_id,
        "event_seq": 2,
        "parent_event": "event_1",
        "event_kind": event_kind,
        "primitive_type": "Message",
        "actor_id": "user_1",
        "actor_type": "human",
        "execution_id": "execution_1",
        "context_version": 2,
        "status": "active",
        "summary": None,
        "content": "Please revise",
        "artifacts": [
            {
                "artifact_id": "artifact_1",
                "name": "report.md",
                "url": "/files/report.md",
            }
        ],
        "created_at": "2026-11-10T08:00:00Z",
    }


def _projection(
    *,
    status: str = "created",
    events: list[dict[str, Any]] | None = None,
) -> ContextCollabStatusProjection:
    projected_events = [_event_projection()] if events is None else events
    return ContextCollabStatusProjection(
        collab_context_id=_CONTEXT_ID,
        context_version=2,
        current_snapshot={
            "status": status,
            "progress": {"current_event_id": "event_2"},
        },
        current_event={"event_id": "event_2"},
        task_progress=TaskProgressProjectionDTO(
            total_events=len(projected_events),
            events=projected_events,
        ),
    )


class _RecordingContextService:
    def __init__(self) -> None:
        self.create_calls = []
        self.get_calls = []
        self.run_calls = []
        self.refresh_calls = []
        self.append_calls = []
        self.projection = _projection()
        self.accepted_event_id = "event_2"

    def create_context(self, command, context):
        self.create_calls.append((command, context))
        return ContextCollabCreatedResult(
            collab_context_id=_CONTEXT_ID,
            context_version=1,
            current_snapshot={
                "status": "created",
                "progress": {"current_event_id": "event_1"},
            },
            current_event={"event_id": "event_1"},
        )

    def get_status(self, collab_context_id, *, trunk_events, context):
        self.get_calls.append((collab_context_id, trunk_events, context))
        return self.projection

    async def run_context(
        self,
        collab_context_id,
        context,
        *,
        todo_execution_id=None,
        todo_execution_token=None,
    ):
        self.run_calls.append(
            (collab_context_id, context, todo_execution_id, todo_execution_token)
        )
        self.projection = _projection(status="in_progress")
        return ContextCollabRunResultDTO(
            collab_context_id=collab_context_id,
            run_id="run_1",
            session_id="session_1",
            context_version=2,
            current_status="in_progress",
        )

    async def refresh_run(self, collab_context_id, context):
        self.refresh_calls.append((collab_context_id, context))
        self.projection = _projection(status="done")
        return self.projection

    def append_event(self, collab_context_id, command, context):
        self.append_calls.append((collab_context_id, command, context))
        return EventAcceptedResult(
            accepted=True,
            collab_context_id=collab_context_id,
            event_id=self.accepted_event_id,
            parent_event=command.parent_event,
            event_kind=command.event_kind,
            primitive_type=command.primitive_type,
            context_version=2,
            current_status="in_progress",
        )


def test_create_context_translates_context_domain_rejection() -> None:
    service = _RecordingContextService()
    failure = ContextCollabValidationError("invalid context")

    def fail_create(command, context):
        raise failure

    service.create_context = fail_create
    plugin = InProcessTodoCollaborationPlugin(service)

    with pytest.raises(TodoCollaborationError, match="context creation") as exc_info:
        plugin.create_context(_context_request())

    assert exc_info.value.__cause__ is failure


def test_create_context_adapts_todo_request_and_preserves_bot_owner() -> None:
    service = _RecordingContextService()
    plugin = InProcessTodoCollaborationPlugin(service)

    result = plugin.create_context(_context_request())

    assert result.collab_context_id == _CONTEXT_ID
    assert result.status == "created"
    command, actor = service.create_calls[0]
    assert command.space.space_id == "7"
    assert command.space.environment == "test"
    assert command.principal_ref.principal_id == "user_1"
    assert command.executor_ref.agent_id == "bot_1"
    assert command.executor_owner_id == "another_user"
    assert command.objective == "Write the report"
    assert [ref.to_dict() for ref in command.resource_refs] == [
        {
            "resource_type": "todo",
            "resource_id": "todo_1",
            "revision": None,
        }
    ]
    assert actor.actor.principal_type == "human"
    assert actor.actor.principal_id == "user_1"
    assert actor.source == "todo"


def test_get_context_translates_missing_context_to_todo_boundary() -> None:
    service = _RecordingContextService()
    failure = ContextCollabNotFoundError("missing context")

    def fail_get(collab_context_id, *, trunk_events, context):
        raise failure

    service.get_status = fail_get
    plugin = InProcessTodoCollaborationPlugin(service)

    with pytest.raises(TodoCollaborationError, match="could not project") as exc_info:
        plugin.get_context(
            collab_context_id=_CONTEXT_ID,
            actor_id="viewer_1",
        )

    assert exc_info.value.__cause__ is failure


def test_get_context_does_not_hide_unexpected_programming_error() -> None:
    service = _RecordingContextService()

    def fail_get(collab_context_id, *, trunk_events, context):
        raise AssertionError("programming bug")

    service.get_status = fail_get
    plugin = InProcessTodoCollaborationPlugin(service)

    with pytest.raises(AssertionError, match="programming bug"):
        plugin.get_context(
            collab_context_id=_CONTEXT_ID,
            actor_id="viewer_1",
        )


def test_get_context_uses_human_actor_and_maps_full_event_projection() -> None:
    service = _RecordingContextService()
    plugin = InProcessTodoCollaborationPlugin(service)

    context = plugin.get_context(
        collab_context_id=_CONTEXT_ID,
        actor_id="viewer_1",
    )

    collab_context_id, trunk_events, actor = service.get_calls[0]
    assert collab_context_id == _CONTEXT_ID
    assert trunk_events is False
    assert actor.actor.principal_id == "viewer_1"
    assert actor.actor.principal_type == "human"
    assert actor.source == "todo"
    assert context.current_event_id == "event_2"
    assert context.events[0].event_type == "task.created"
    assert context.events[0].context_version == 2
    assert context.events[0].artifacts[0].url == "/files/report.md"


@pytest.mark.asyncio
async def test_run_context_calls_service_then_refreshes_with_same_actor() -> None:
    service = _RecordingContextService()
    plugin = InProcessTodoCollaborationPlugin(service)

    context = await plugin.run_context(
        collab_context_id=_CONTEXT_ID,
        actor_id="user_1",
    )

    assert context.status == "in_progress"
    assert service.run_calls[0][0] == _CONTEXT_ID
    run_actor = service.run_calls[0][1]
    get_actor = service.get_calls[0][2]
    assert run_actor == get_actor
    assert run_actor.actor.principal_id == "user_1"
    assert run_actor.source == "todo"


@pytest.mark.asyncio
async def test_refresh_context_run_calls_service_with_human_actor() -> None:
    service = _RecordingContextService()
    plugin = InProcessTodoCollaborationPlugin(service)

    context = await plugin.refresh_context_run(
        collab_context_id=_CONTEXT_ID,
        actor_id="user_1",
    )

    assert context.status == "done"
    collab_context_id, actor = service.refresh_calls[0]
    assert collab_context_id == _CONTEXT_ID
    assert actor.actor.principal_id == "user_1"
    assert actor.actor.principal_type == "human"
    assert actor.source == "todo"


@pytest.mark.asyncio
async def test_refresh_context_run_translates_runtime_failure() -> None:
    service = _RecordingContextService()
    failure = ContextCollabRuntimeError("runtime unavailable")

    async def fail_refresh(collab_context_id, context):
        raise failure

    service.refresh_run = fail_refresh
    plugin = InProcessTodoCollaborationPlugin(service)

    with pytest.raises(TodoCollaborationError, match="refresh") as exc_info:
        await plugin.refresh_context_run(
            collab_context_id=_CONTEXT_ID,
            actor_id="user_1",
        )

    assert exc_info.value.__cause__ is failure


@pytest.mark.asyncio
async def test_run_context_translates_context_domain_rejection() -> None:
    service = _RecordingContextService()
    failure = ContextCollabValidationError("wrong state")

    async def fail_run(
        collab_context_id,
        context,
        *,
        todo_execution_id=None,
        todo_execution_token=None,
    ):
        raise failure

    service.run_context = fail_run
    plugin = InProcessTodoCollaborationPlugin(service)

    with pytest.raises(TodoCollaborationError, match="run request") as exc_info:
        await plugin.run_context(
            collab_context_id=_CONTEXT_ID,
            actor_id="user_1",
        )

    assert exc_info.value.__cause__ is failure
    assert service.get_calls == []


@pytest.mark.asyncio
async def test_run_context_translates_runtime_failure_to_todo_boundary() -> None:
    service = _RecordingContextService()

    async def fail_run(
        collab_context_id,
        context,
        *,
        todo_execution_id=None,
        todo_execution_token=None,
    ):
        raise ContextCollabRuntimeError("runtime unavailable")

    service.run_context = fail_run
    plugin = InProcessTodoCollaborationPlugin(service)

    with pytest.raises(TodoCollaborationError, match="could not start"):
        await plugin.run_context(
            collab_context_id=_CONTEXT_ID,
            actor_id="user_1",
        )

    assert service.get_calls == []


@pytest.mark.parametrize(
    "failure",
    [
        ContextCollabVersionConflict("stale version"),
        ContextCollabParentEventError("missing parent"),
    ],
)
def test_append_event_translates_context_domain_rejection(failure) -> None:
    service = _RecordingContextService()

    def fail_append(collab_context_id, command, context):
        raise failure

    service.append_event = fail_append
    plugin = InProcessTodoCollaborationPlugin(service)

    with pytest.raises(TodoCollaborationError, match="Todo event") as exc_info:
        plugin.append_event(
            collab_context_id=_CONTEXT_ID,
            request=TodoCollaborationEventRequest(
                expected_version=1,
                event_kind="user_interaction.posted",
                primitive_type="Message",
                actor_id="user_1",
                actor_type="human",
                parent_event="event_1",
                visibility="workspace",
                payload={"content": "Please revise"},
            ),
        )

    assert exc_info.value.__cause__ is failure
    assert service.get_calls == []


def test_append_event_adapts_command_and_returns_accepted_projection() -> None:
    service = _RecordingContextService()
    service.projection = _projection(
        status="in_progress",
        events=[_event_projection(event_kind="user_interaction.posted")],
    )
    plugin = InProcessTodoCollaborationPlugin(service)
    request = TodoCollaborationEventRequest(
        expected_version=1,
        event_kind="user_interaction.posted",
        primitive_type="Message",
        actor_id="user_1",
        actor_type="human",
        parent_event="event_1",
        visibility="workspace",
        payload={"content": "Please revise"},
    )

    event = plugin.append_event(
        collab_context_id=_CONTEXT_ID,
        request=request,
    )

    collab_context_id, command, actor = service.append_calls[0]
    assert collab_context_id == _CONTEXT_ID
    assert command.expected_version == 1
    assert command.event_kind == "user_interaction.posted"
    assert command.actor_ref.principal_id == "user_1"
    assert command.payload == {"content": "Please revise"}
    assert actor.actor == command.actor_ref
    assert actor.source == "todo"
    assert service.get_calls[0][2] == actor
    assert event.event_type == "user_interaction.posted"
    assert event.content == "Please revise"


def test_append_event_rejects_missing_accepted_event_projection() -> None:
    service = _RecordingContextService()
    service.accepted_event_id = "event_missing"
    plugin = InProcessTodoCollaborationPlugin(service)

    with pytest.raises(TodoCollaborationError, match="event_missing"):
        plugin.append_event(
            collab_context_id=_CONTEXT_ID,
            request=TodoCollaborationEventRequest(
                expected_version=1,
                event_kind="user_interaction.posted",
                primitive_type="Message",
                actor_id="user_1",
                actor_type="human",
                parent_event="event_1",
                visibility="workspace",
                payload={"content": "Please revise"},
            ),
        )
