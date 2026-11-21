from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import pytest
from fastapi import Request, Response
from pydantic import ValidationError as PydanticValidationError

from agentclaw.community.adapters.http.openapi_v1.task.todos.router import (
    create_todo,
    create_todo_interaction,
    get_dima_work_item_draft,
    get_todo,
    list_dima_projects,
    list_dima_work_items,
    list_todo_artifacts,
    list_todo_events,
    list_todo_interactions,
    list_todos,
    read_todo_artifact_content,
    run_todo,
    submit_todo_acceptance,
)
from agentclaw.community.adapters.http.openapi_v1.task.todos.schemas import (
    CreateTodoInteractionRequest,
    CreateTodoRequest,
    SubmitTodoAcceptanceRequest,
)
from agentclaw.community.adapters.http.openapi_v1.responses import mapped_error_response
from agentclaw.community.core.task.todos.artifact_models import (
    TodoArtifactDownload,
    TodoArtifactKind,
    TodoArtifactRecord,
)
from agentclaw.community.core.task.todos.source_models import (
    TodoSourceDraft,
    TodoSourceInput,
    TodoSourceProject,
    TodoSourceWorkItem,
)
from agentclaw.community.core.task.todos.errors import (
    TodoSourceAlreadyImportedError,
)
from agentclaw.community.core.task.todos.models import (
    AcceptanceVerdict,
    TodoArtifactRecord as TodoEventArtifactRecord,
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


def _artifact() -> TodoEventArtifactRecord:
    return TodoEventArtifactRecord(
        artifact_id="artifact_1",
        name="report.docx",
        url="/files/report.docx",
    )


def _event() -> TodoEventRecord:
    return TodoEventRecord(
        event_id="event_3",
        event_seq=3,
        event_type="task.completed",
        actor_id="bot_1",
        actor_type="bot",
        content="done",
        execution_id="execution_1",
        context_version=2,
        status="pending_acceptance",
        artifacts=(_artifact(),),
        created_at="2026-11-10T08:00:00Z",
        payload={"content": "done", "result_kind": "text"},
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
        event_type="user_interaction.posted",
        actor_id="human_1",
        actor_type="human",
        content="Please revise section 2",
        execution_id="execution_1",
        context_version=2,
        status=None,
        artifacts=(),
        created_at="2026-11-10T07:55:00Z",
        payload={"content": "Please revise section 2"},
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
        return _todo(status=TodoStatus.PENDING, collab_context_id=None)

    async def start_todo(self, **kwargs):
        self._record("start_todo", **kwargs)
        return _todo()

    def list_events(self, **kwargs):
        self._record("list_events", **kwargs)
        return (_event(),)

    def list_interactions(self, **kwargs):
        self._record("list_interactions", **kwargs)
        return (_interaction_event(),)

    async def create_interaction(self, **kwargs):
        self._record("create_interaction", **kwargs)
        return TodoEventRecord(
            event_id="event_4",
            event_seq=4,
            event_type="user_interaction.posted",
            actor_id="human_1",
            actor_type="human",
            content="Please include the rollback plan",
            execution_id="execution_1",
            context_version=3,
            status="in_progress",
            artifacts=(),
            created_at="2026-11-10T08:05:00Z",
            payload={"content": "Please include the rollback plan"},
        )

    def submit_acceptance(self, **kwargs):
        self._record("submit_acceptance", **kwargs)
        status = (
            TodoStatus.COMPLETED
            if kwargs["verdict"] == AcceptanceVerdict.APPROVED
            else TodoStatus.IN_PROGRESS
        )
        return _todo(status=status)


@pytest.mark.asyncio
async def test_list_todos_maps_product_page_without_internal_fields():
    service = _RecordedService()

    payload = await list_todos(
        request=_request("GET", "/openapi/v1/collaboration/tasks/todos"),
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
        request=_request("POST", "/openapi/v1/collaboration/tasks/todos"),
        response=response,
        principal={"user_id": "human_1"},
        user_id="human_1",
        service=service,
    )

    assert response.status_code == 201
    assert payload.code == 201000
    assert payload.message == "Created"
    assert payload.data.status == TodoStatus.PENDING
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
async def test_create_todo_maps_dima_source_identity():
    service = _RecordedService()

    await create_todo(
        body=CreateTodoRequest(
            space_id=42,
            title="Prepare release notes",
            description="Summarize the changes",
            bot_id="bot_1",
            source={
                "type": "dima",
                "container_id": "P1",
                "item_id": "work_1",
                "url": "https://dima.example/items/work_1",
                "updated_at": "2026-11-20T12:00:00Z",
            },
        ),
        request=_request("POST", "/openapi/v1/collaboration/tasks/todos"),
        response=Response(),
        principal={"user_id": "human_1"},
        user_id="human_1",
        service=service,
    )

    assert service.calls[-1]["source"] == TodoSourceInput(
        source_type="dima",
        container_id="P1",
        item_id="work_1",
        url="https://dima.example/items/work_1",
        updated_at="2026-11-20T12:00:00Z",
    )


def test_duplicate_source_error_exposes_existing_todo_id():
    response = mapped_error_response(
        TodoSourceAlreadyImportedError("todo_existing"),
        _request("POST", "/openapi/v1/collaboration/tasks/todos"),
    )

    assert response is not None
    assert response.status_code == 409
    payload = json.loads(response.body)
    assert payload["code"] == 409000
    assert payload["message"] == "Dima work item already imported"
    assert payload["data"] == {"existing_todo_id": "todo_existing"}


@pytest.mark.asyncio
async def test_get_and_run_use_authenticated_actor():
    service = _RecordedService()

    detail = await get_todo(
        todo_id="todo_1",
        request=_request("GET", "/openapi/v1/collaboration/tasks/todos/todo_1"),
        principal={"user_id": "human_1"},
        user_id="human_1",
        service=service,
    )
    started = await run_todo(
        todo_id="todo_1",
        request=_request("POST", "/openapi/v1/collaboration/tasks/todos/todo_1/run"),
        principal={"user_id": "human_1"},
        user_id="human_1",
        service=service,
    )

    assert detail.data.todo_id == "todo_1"
    assert started.data.status == TodoStatus.IN_PROGRESS
    assert service.calls == [
        {"op": "get_todo", "todo_id": "todo_1", "actor_id": "human_1"},
        {"op": "start_todo", "todo_id": "todo_1", "actor_id": "human_1"},
    ]


@pytest.mark.asyncio
async def test_events_and_interactions_project_b_event_records():
    service = _RecordedService()

    events = await list_todo_events(
        todo_id="todo_1",
        request=_request("GET", "/openapi/v1/collaboration/tasks/todos/todo_1/events"),
        principal={"user_id": "human_1"},
        user_id="human_1",
        service=service,
    )
    history = await list_todo_interactions(
        todo_id="todo_1",
        request=_request(
            "GET", "/openapi/v1/collaboration/tasks/todos/todo_1/interactions"
        ),
        principal={"user_id": "human_1"},
        user_id="human_1",
        service=service,
    )

    assert events.data[0].event_id == "event_3"
    assert events.data[0].execution_id == "execution_1"
    assert events.data[0].context_version == 2
    assert events.data[0].status == "pending_acceptance"
    assert events.data[0].artifacts[0].name == "report.docx"
    assert events.data[0].artifacts[0].url == "/files/report.docx"
    assert "payload" not in events.data[0].model_dump()
    assert service.calls == [
        {"op": "list_events", "todo_id": "todo_1", "actor_id": "human_1"},
        {"op": "list_interactions", "todo_id": "todo_1", "actor_id": "human_1"},
    ]
    assert history.code == 200000
    assert history.data[0].event_type == "user_interaction.posted"
    assert history.data[0].payload == {"content": "Please revise section 2"}


@pytest.mark.asyncio
async def test_create_interaction_uses_message_only_and_returns_created_event():
    service = _RecordedService()
    response = Response()

    payload = await create_todo_interaction(
        todo_id="todo_1",
        body=CreateTodoInteractionRequest(message="Please include the rollback plan"),
        request=_request(
            "POST", "/openapi/v1/collaboration/tasks/todos/todo_1/interactions"
        ),
        response=response,
        principal={"user_id": "human_1"},
        user_id="human_1",
        service=service,
    )

    assert response.status_code == 201
    assert payload.code == 201000
    assert payload.data.event_id == "event_4"
    assert payload.data.event_type == "user_interaction.posted"
    assert payload.data.content == "Please include the rollback plan"
    assert payload.data.payload == {"content": "Please include the rollback plan"}
    assert service.calls == [
        {
            "op": "create_interaction",
            "todo_id": "todo_1",
            "actor_id": "human_1",
            "author_name": None,
            "message": "Please include the rollback plan",
            "reply_to_event_id": None,
            "mentions": (),
        }
    ]


@pytest.mark.asyncio
async def test_create_interaction_maps_reply_and_structured_mentions():
    service = _RecordedService()
    response = Response()

    await create_todo_interaction(
        todo_id="todo_1",
        body=CreateTodoInteractionRequest(
            message="Please review this floor",
            reply_to_event_id="event_2",
            mentions=[{"actor_type": "bot", "actor_id": "bot_2"}],
        ),
        request=_request(
            "POST", "/openapi/v1/collaboration/tasks/todos/todo_1/interactions"
        ),
        response=response,
        principal={"user_id": "human_1"},
        user_id="human_1",
        service=service,
    )

    call = service.calls[0]
    assert call["reply_to_event_id"] == "event_2"
    assert len(call["mentions"]) == 1
    assert call["mentions"][0].actor_type.value == "bot"
    assert call["mentions"][0].actor_id == "bot_2"


def test_create_interaction_rejects_non_bot_mention_actor():
    with pytest.raises(PydanticValidationError):
        CreateTodoInteractionRequest(
            message="invalid",
            mentions=[{"actor_type": "human", "actor_id": "human_2"}],
        )


@pytest.mark.asyncio
async def test_acceptance_returns_updated_todo_and_passes_verdict():
    service = _RecordedService()

    payload = await submit_todo_acceptance(
        todo_id="todo_1",
        body=SubmitTodoAcceptanceRequest(
            decision_id="decision_1",
            verdict=AcceptanceVerdict.APPROVED,
            message="Looks good",
        ),
        request=_request(
            "POST", "/openapi/v1/collaboration/tasks/todos/todo_1/acceptance"
        ),
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
            "decision_id": "decision_1",
            "message": "Looks good",
            "targets": (),
        }
    ]


@pytest.mark.asyncio
async def test_rejection_forwards_full_message_and_stable_targets():
    service = _RecordedService()

    payload = await submit_todo_acceptance(
        todo_id="todo_1",
        body=SubmitTodoAcceptanceRequest(
            decision_id="decision_reject_1",
            verdict=AcceptanceVerdict.REJECTED,
            message="Please correct the enterprise price",
            targets=[
                {"kind": "event", "id": "event_2", "floor": 2},
                {"kind": "artifact", "id": "artifact_1", "floor": 3},
            ],
        ),
        request=_request(
            "POST", "/openapi/v1/collaboration/tasks/todos/todo_1/acceptance"
        ),
        principal={"user_id": "human_1"},
        user_id="human_1",
        service=service,
    )

    assert payload.data.status == TodoStatus.IN_PROGRESS
    call = service.calls[0]
    assert call["op"] == "submit_acceptance"
    assert call["decision_id"] == "decision_reject_1"
    assert call["message"] == "Please correct the enterprise price"
    assert [
        (target.kind.value, target.target_id, target.floor)
        for target in call["targets"]
    ] == [
        ("event", "event_2", 2),
        ("artifact", "artifact_1", 3),
    ]


def _persisted_file_artifact() -> TodoArtifactRecord:
    return TodoArtifactRecord(
        artifact_id="todoartifact_1",
        todo_id="todo_1",
        collab_context_id="context_todo_1",
        execution_id="todoexec_1",
        artifact_name="Release report",
        artifact_kind=TodoArtifactKind.FILE,
        content={
            "kind": "file",
            "storage_id": "task-artifacts/todos/todo_1/report.pdf",
            "file_name": "release report.pdf",
            "file_type": "application/pdf",
            "size": 4,
            "is_final_artifact": True,
        },
        content_hash="hash",
        creator="bot_1",
        created_at=NOW,
        updated_at=NOW,
    )


class _ArtifactService:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def list_artifacts(self, **kwargs):
        self.calls.append({"op": "list_artifacts", **kwargs})
        return (_persisted_file_artifact(),)

    def read_file(self, **kwargs):
        self.calls.append({"op": "read_file", **kwargs})
        return TodoArtifactDownload(
            record=_persisted_file_artifact(),
            payload=b"%PDF",
        )


@pytest.mark.asyncio
async def test_list_artifacts_returns_public_content_without_storage_id():
    service = _ArtifactService()

    payload = await list_todo_artifacts(
        todo_id="todo_1",
        request=_request(
            "GET", "/openapi/v1/collaboration/tasks/todos/todo_1/artifacts"
        ),
        principal={"user_id": "human_1"},
        user_id="human_1",
        service=service,
    )

    assert payload.code == 200000
    assert payload.request_id == "trace-todo"
    assert payload.data[0].kind.value == TodoArtifactKind.FILE.value
    assert payload.data[0].todo_execution_id == "todoexec_1"
    assert not hasattr(payload.data[0], "execution_id")
    assert payload.data[0].content == {
        "kind": "file",
        "file_name": "release report.pdf",
        "file_type": "application/pdf",
        "size": 4,
        "is_final_artifact": True,
    }
    assert service.calls == [
        {
            "op": "list_artifacts",
            "todo_id": "todo_1",
            "actor_id": "human_1",
        }
    ]


@pytest.mark.asyncio
async def test_read_artifact_content_sets_preview_headers_and_passes_actor():
    service = _ArtifactService()

    response = await read_todo_artifact_content(
        todo_id="todo_1",
        artifact_id="todoartifact_1",
        principal={"user_id": "human_1"},
        user_id="human_1",
        disposition="inline",
        service=service,
    )

    assert response.body == b"%PDF"
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["content-length"] == "4"
    assert response.headers["content-disposition"] == (
        "inline; filename*=UTF-8''release%20report.pdf"
    )
    assert service.calls == [
        {
            "op": "read_file",
            "todo_id": "todo_1",
            "artifact_id": "todoartifact_1",
            "actor_id": "human_1",
        }
    ]


def _source_request(path: str) -> Request:
    request = Request(
        scope={
            "type": "http",
            "http_version": "1.1",
            "method": "GET",
            "scheme": "https",
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"user_id=human_1",
            "headers": [
                (b"cookie", b"IAM_TOKEN=iam-token"),
                (b"x-source-test", b"present"),
            ],
            "server": ("example.test", 443),
            "client": ("127.0.0.1", 1234),
        }
    )
    request.state.trace_id = "trace-todo-source"
    return request


class _RecordedSourceService:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def list_projects(self, **kwargs):
        self.calls.append({"op": "list_projects", **kwargs})
        return (TodoSourceProject(id="P1", name="Dima project"),)

    def list_work_items(self, **kwargs):
        self.calls.append({"op": "list_work_items", **kwargs})
        return (
            TodoSourceWorkItem(
                id="2026112000000000001",
                project_id="P1",
                category="req",
                title="Dima requirement",
                status="处理中",
                updated_at="2026-11-20T12:00:00Z",
            ),
        )

    def load_work_item_draft(self, **kwargs):
        self.calls.append({"op": "load_work_item_draft", **kwargs})
        return TodoSourceDraft(
            source_type="dima",
            project_id="P1",
            work_item_id="2026112000000000001",
            category="req",
            title="Dima requirement",
            description="Imported description",
            source_url="https://dima.example/items/1",
            source_updated_at="2026-11-20T12:00:00Z",
        )


@pytest.mark.asyncio
async def test_dima_source_routes_forward_verified_human_request_context():
    service = _RecordedSourceService()
    principal = {"user_id": "human_1"}

    projects = await list_dima_projects(
        request=_source_request(
            "/openapi/v1/collaboration/tasks/todos/sources/dima/projects"
        ),
        principal=principal,
        user_id="human_1",
        service=service,
    )
    items = await list_dima_work_items(
        project_id="P1",
        request=_source_request(
            "/openapi/v1/collaboration/tasks/todos/sources/dima/projects/P1/work-items"
        ),
        principal=principal,
        user_id="human_1",
        query="requirement",
        service=service,
    )
    draft = await get_dima_work_item_draft(
        project_id="P1",
        work_item_id="2026112000000000001",
        request=_source_request(
            "/openapi/v1/collaboration/tasks/todos/sources/dima/projects/P1/"
            "work-items/2026112000000000001"
        ),
        principal=principal,
        user_id="human_1",
        service=service,
    )

    assert projects.data[0].model_dump() == {"id": "P1", "name": "Dima project"}
    assert items.data[0].category == "req"
    assert draft.data.title == "Dima requirement"
    assert draft.data.description == "Imported description"
    assert [call["op"] for call in service.calls] == [
        "list_projects",
        "list_work_items",
        "load_work_item_draft",
    ]
    for call in service.calls:
        assert call["actor_id"] == "human_1"
        auth_request = call["auth_request"]
        assert auth_request.cookies["IAM_TOKEN"] == "iam-token"
        assert auth_request.headers["x-source-test"] == "present"
        assert auth_request.verified_actor_user_id == "human_1"
        assert auth_request.base_url == "https://example.test/"
    assert service.calls[1]["project_id"] == "P1"
    assert service.calls[1]["query"] == "requirement"
    assert service.calls[2]["work_item_id"] == "2026112000000000001"
