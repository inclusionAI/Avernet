from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest
from fastapi import Request, Response

from agentclaw.community.adapters.http.openapi_v1.task.todos.router import (
    create_todo,
    create_todo_interaction,
    get_todo,
    list_todo_events,
    list_todo_interactions,
    list_todos,
    run_todo,
    submit_todo_acceptance,
    update_todo,
)
from agentclaw.community.adapters.http.openapi_v1.task.todos.schemas import (
    CreateTodoInteractionRequest,
    CreateTodoRequest,
    SubmitTodoAcceptanceRequest,
    UpdateTodoRequest,
)
from agentclaw.community.core.task.todos.models import (
    AcceptanceVerdict,
    TodoArtifactRecord,
    TodoEventRecord,
    TodoPage,
    TodoRecord,
    TodoStatus,
)

NOW = datetime(2026, 11, 10, 8, 30, tzinfo=timezone.utc)


def _request(method: str, path: str) -> Request:
    request = Request(scope={"type": "http", "method": method, "path": path})
    request.state.trace_id = "trace-todo"
    return request


def _artifact() -> TodoArtifactRecord:
    return TodoArtifactRecord(
        artifact_id="artifact_1",
        name="report.docx",
        url="/files/report.docx",
    )


def _event() -> TodoEventRecord:
    return TodoEventRecord(
        event_id="event_3",
        event_seq=3,
        event_type="execution.status",
        actor_id="bot_1",
        actor_type="bot",
        content="done",
        execution_id="execution_1",
        context_version="v2",
        status="pending_acceptance",
        artifacts=(_artifact(),),
        created_at="2026-11-10T08:00:00Z",
    )


def _todo(
    *,
    status: TodoStatus = TodoStatus.IN_PROGRESS,
    collab_context_id: str | None = "context_todo_1",
) -> TodoRecord:
    return TodoRecord(
        todo_id="todo_1",
        space_id=42,
        title="Prepare release notes",
        description="Summarize the changes",
        bot_id="bot_1",
        bot_name="Release Bot",
        status=status,
        collab_context_id=collab_context_id,
        created_by="human_1",
        created_at=NOW,
        updated_at=NOW,
    )


def _interaction_event() -> TodoEventRecord:
    return TodoEventRecord(
        event_id="event_2",
        event_seq=2,
        event_type="human_interaction",
        actor_id="human_1",
        actor_type="human",
        content="Please revise section 2",
        execution_id="execution_1",
        context_version="v2",
        status=None,
        artifacts=(),
        created_at="2026-11-10T07:55:00Z",
    )


class _PageParams:
    def __init__(self, page: int, page_size: int) -> None:
        self.page = page
        self.page_size = page_size


class _RecordedService:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def _record(self, op: str, **kwargs: Any):
        self.calls.append({"op": op, **kwargs})

    def list_todos(self, **kwargs):
        self._record("list_todos", **kwargs)
        return TodoPage(total=1, items=(_todo(),))

    def get_todo(self, **kwargs):
        self._record("get_todo", **kwargs)
        return _todo()

    def create_todo(self, **kwargs):
        self._record("create_todo", **kwargs)
        return _todo(status=TodoStatus.DRAFT, collab_context_id=None)

    def update_todo(self, **kwargs):
        self._record("update_todo", **kwargs)
        return _todo(status=TodoStatus.DRAFT, collab_context_id=None)

    def start_todo(self, **kwargs):
        self._record("start_todo", **kwargs)
        return _todo()

    def list_events(self, **kwargs):
        self._record("list_events", **kwargs)
        return (_event(),)

    def list_interactions(self, **kwargs):
        self._record("list_interactions", **kwargs)
        return (_interaction_event(),)

    def create_interaction(self, **kwargs):
        self._record("create_interaction", **kwargs)
        return TodoEventRecord(
            event_id="event_4",
            event_seq=4,
            event_type="human_interaction",
            actor_id="human_1",
            actor_type="human",
            content="Please include the rollback plan",
            execution_id="execution_1",
            context_version="v3",
            status="in_progress",
            artifacts=(),
            created_at="2026-11-10T08:05:00Z",
        )

    def submit_acceptance(self, **kwargs):
        self._record("submit_acceptance", **kwargs)
        return _todo(status=TodoStatus.COMPLETED)


@pytest.mark.asyncio
async def test_list_todos_maps_product_page_without_internal_fields():
    service = _RecordedService()

    payload = await list_todos(
        request=_request("GET", "/openapi/v1/todos"),
        page_params=_PageParams(page=2, page_size=25),
        space_id=42,
        principal={"user_id": "human_1"},
        user_id="human_1",
        keyword="release",
        status=TodoStatus.IN_PROGRESS,
        bot_id="bot_2",
        service=service,
    )

    assert payload.code == 200000
    assert payload.request_id == "trace-todo"
    assert payload.data.total == 1
    item = payload.data.items[0]
    assert item.todo_id == "todo_1"
    assert item.collab_context_id == "context_todo_1"
    assert item.status == TodoStatus.IN_PROGRESS
    assert "created_by" not in item.model_dump()
    assert service.calls == [
        {
            "op": "list_todos",
            "space_id": 42,
            "actor_id": "human_1",
            "keyword": "release",
            "status": TodoStatus.IN_PROGRESS,
            "bot_id": "bot_2",
            "page": 2,
            "page_size": 25,
        }
    ]


@pytest.mark.asyncio
async def test_create_todo_returns_201_and_passes_body_fields():
    service = _RecordedService()
    response = Response()

    payload = await create_todo(
        body=CreateTodoRequest(
            space_id=42,
            title="Prepare release notes",
            description="Summarize the changes",
            bot_id="bot_1",
        ),
        request=_request("POST", "/openapi/v1/todos"),
        response=response,
        principal={"user_id": "human_1"},
        user_id="human_1",
        service=service,
    )

    assert response.status_code == 201
    assert payload.code == 201000
    assert payload.message == "Created"
    assert payload.data.status == TodoStatus.DRAFT
    assert payload.data.collab_context_id is None
    assert service.calls == [
        {
            "op": "create_todo",
            "space_id": 42,
            "actor_id": "human_1",
            "title": "Prepare release notes",
            "description": "Summarize the changes",
            "bot_id": "bot_1",
        }
    ]


@pytest.mark.asyncio
async def test_get_update_and_run_use_authenticated_actor():
    service = _RecordedService()

    detail = await get_todo(
        todo_id="todo_1",
        request=_request("GET", "/openapi/v1/todos/todo_1"),
        principal={"user_id": "human_1"},
        user_id="human_1",
        service=service,
    )
    updated = await update_todo(
        todo_id="todo_1",
        body=UpdateTodoRequest(
            title="Updated title",
            description="Updated description",
            bot_id="bot_2",
        ),
        request=_request("PATCH", "/openapi/v1/todos/todo_1"),
        principal={"user_id": "human_1"},
        user_id="human_1",
        service=service,
    )
    started = await run_todo(
        todo_id="todo_1",
        request=_request("POST", "/openapi/v1/todos/todo_1/run"),
        principal={"user_id": "human_1"},
        user_id="human_1",
        service=service,
    )

    assert detail.data.todo_id == "todo_1"
    assert updated.data.todo_id == "todo_1"
    assert started.data.status == TodoStatus.IN_PROGRESS
    assert service.calls == [
        {"op": "get_todo", "todo_id": "todo_1", "actor_id": "human_1"},
        {
            "op": "update_todo",
            "todo_id": "todo_1",
            "actor_id": "human_1",
            "title": "Updated title",
            "description": "Updated description",
            "bot_id": "bot_2",
        },
        {"op": "start_todo", "todo_id": "todo_1", "actor_id": "human_1"},
    ]


@pytest.mark.asyncio
async def test_events_and_interactions_project_b_event_records():
    service = _RecordedService()

    events = await list_todo_events(
        todo_id="todo_1",
        request=_request("GET", "/openapi/v1/todos/todo_1/events"),
        principal={"user_id": "human_1"},
        user_id="human_1",
        service=service,
    )
    history = await list_todo_interactions(
        todo_id="todo_1",
        request=_request("GET", "/openapi/v1/todos/todo_1/interactions"),
        principal={"user_id": "human_1"},
        user_id="human_1",
        service=service,
    )

    assert events.data[0].event_id == "event_3"
    assert events.data[0].execution_id == "execution_1"
    assert events.data[0].context_version == "v2"
    assert events.data[0].status == "pending_acceptance"
    assert events.data[0].artifacts[0].name == "report.docx"
    assert events.data[0].artifacts[0].url == "/files/report.docx"
    assert service.calls == [
        {"op": "list_events", "todo_id": "todo_1", "actor_id": "human_1"},
        {"op": "list_interactions", "todo_id": "todo_1", "actor_id": "human_1"},
    ]
    assert history.code == 200000
    assert history.data[0].event_type == "human_interaction"


@pytest.mark.asyncio
async def test_create_interaction_uses_message_only_and_returns_created_event():
    service = _RecordedService()
    response = Response()

    payload = await create_todo_interaction(
        todo_id="todo_1",
        body=CreateTodoInteractionRequest(message="Please include the rollback plan"),
        request=_request("POST", "/openapi/v1/todos/todo_1/interactions"),
        response=response,
        principal={"user_id": "human_1"},
        user_id="human_1",
        service=service,
    )

    assert response.status_code == 201
    assert payload.code == 201000
    assert payload.data.event_id == "event_4"
    assert payload.data.event_type == "human_interaction"
    assert payload.data.content == "Please include the rollback plan"
    assert service.calls == [
        {
            "op": "create_interaction",
            "todo_id": "todo_1",
            "actor_id": "human_1",
            "author_name": None,
            "message": "Please include the rollback plan",
        }
    ]


@pytest.mark.asyncio
async def test_acceptance_returns_updated_todo_and_passes_verdict():
    service = _RecordedService()

    payload = await submit_todo_acceptance(
        todo_id="todo_1",
        body=SubmitTodoAcceptanceRequest(
            verdict=AcceptanceVerdict.APPROVED,
            message="Looks good",
        ),
        request=_request("POST", "/openapi/v1/todos/todo_1/acceptance"),
        principal={"user_id": "human_1"},
        user_id="human_1",
        service=service,
    )

    assert payload.code == 200000
    assert payload.data.status == TodoStatus.COMPLETED
    assert service.calls == [
        {
            "op": "submit_acceptance",
            "todo_id": "todo_1",
            "actor_id": "human_1",
            "verdict": AcceptanceVerdict.APPROVED,
            "message": "Looks good",
        }
    ]
