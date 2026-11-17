from __future__ import annotations

from datetime import datetime, timezone
from io import BytesIO
from typing import Any

import pytest
from fastapi import HTTPException, Request, UploadFile

from agentclaw.community.adapters.http.task.todos.artifact_routes import (
    CompleteTodoRequest,
    complete_todo_execution,
    ReportTodoArtifactRequest,
    report_todo_artifact,
    upload_todo_artifact_file,
)
from agentclaw.community.core.task.todos.artifact_models import (
    TodoArtifactKind,
    TodoArtifactRecord,
)

NOW = datetime(2026, 11, 12, tzinfo=timezone.utc)


def _request(path: str) -> Request:
    request = Request(scope={"type": "http", "method": "POST", "path": path})
    request.state.trace_id = "trace-todo-artifact"
    return request


def _record(kind: TodoArtifactKind, content: dict[str, object]) -> TodoArtifactRecord:
    return TodoArtifactRecord(
        artifact_id="todoartifact_1",
        todo_id="todo_1",
        collab_context_id="context_1",
        execution_id="todoexec_1",
        artifact_name="Result",
        artifact_kind=kind,
        content=content,
        content_hash="hash",
        creator="bot_1",
        created_at=NOW,
        updated_at=NOW,
    )


class _Service:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def report_artifact(self, **kwargs):
        self.calls.append(("report", kwargs))
        return _record(
            TodoArtifactKind.TEXT,
            {"kind": "text", "text": "done", "is_final_artifact": True},
        )

    def report_completion(self, **kwargs):
        self.calls.append(("complete", kwargs))

    def upload_file(self, **kwargs):
        self.calls.append(("upload", kwargs))
        return _record(
            TodoArtifactKind.FILE,
            {
                "kind": "file",
                "storage_id": "task-artifacts/todos/todo_1/object",
                "file_name": "report.txt",
                "file_type": "text/plain",
                "size": 7,
                "is_final_artifact": False,
            },
        )


@pytest.mark.asyncio
async def test_report_route_passes_execution_credential_and_returns_public_record():
    service = _Service()

    response = await report_todo_artifact(
        todo_id="todo_1",
        body=ReportTodoArtifactRequest(
            todo_execution_id="todoexec_1",
            collab_context_id="context_from_untrusted_legacy_client",
            artifact_name="Result",
            kind="text",
            content={"text": "done"},
            is_final_artifact=True,
        ),
        request=_request("/api/v1/collaboration/tasks/todos/todo_1/report"),
        execution_token="secret-token",
        service=service,
    )

    assert response.request_id == "trace-todo-artifact"
    assert response.data.kind.value == TodoArtifactKind.TEXT.value
    assert response.data.todo_execution_id == "todoexec_1"
    assert not hasattr(response.data, "execution_id")
    assert response.data.content == {
        "kind": "text",
        "text": "done",
        "is_final_artifact": True,
    }
    assert service.calls == [
        (
            "report",
            {
                "todo_id": "todo_1",
                "todo_execution_id": "todoexec_1",
                "execution_token": "secret-token",
                "artifact_name": "Result",
                "artifact_kind": "text",
                "content": {"text": "done"},
                "is_final_artifact": True,
            },
        )
    ]


@pytest.mark.asyncio
async def test_complete_route_passes_execution_credential_and_returns_acknowledgement():
    service = _Service()

    response = await complete_todo_execution(
        todo_id="todo_1",
        body=CompleteTodoRequest(
            todo_execution_id="todoexec_1",
            collab_context_id="context_from_untrusted_legacy_client",
        ),
        request=_request("/api/v1/collaboration/tasks/todos/todo_1/complete"),
        execution_token="secret-token",
        service=service,
    )

    assert response.request_id == "trace-todo-artifact"
    assert response.data.accepted is True
    assert response.data.todo_id == "todo_1"
    assert response.data.todo_execution_id == "todoexec_1"
    assert service.calls == [
        (
            "complete",
            {
                "todo_id": "todo_1",
                "todo_execution_id": "todoexec_1",
                "execution_token": "secret-token",
            },
        )
    ]


def test_complete_request_accepts_legacy_execution_id_during_compatibility_window():
    body = CompleteTodoRequest(
        execution_id="todoexec_legacy",
        collab_context_id="context_legacy",
    )

    assert body.todo_execution_id == "todoexec_legacy"


def test_complete_request_rejects_conflicting_execution_identifiers():
    with pytest.raises(ValueError, match="must match"):
        CompleteTodoRequest(
            todo_execution_id="todoexec_new",
            execution_id="todoexec_old",
        )


def test_report_request_accepts_legacy_execution_id_during_compatibility_window():
    body = ReportTodoArtifactRequest(
        execution_id="todoexec_legacy",
        collab_context_id="context_legacy",
        artifact_name="Result",
        kind="text",
        content={"text": "done"},
    )

    assert body.todo_execution_id == "todoexec_legacy"


def test_report_request_rejects_conflicting_execution_identifiers():
    with pytest.raises(ValueError, match="must match"):
        ReportTodoArtifactRequest(
            todo_execution_id="todoexec_new",
            execution_id="todoexec_old",
            artifact_name="Result",
            kind="text",
            content={"text": "done"},
        )


@pytest.mark.asyncio
async def test_file_route_passes_multipart_fields_and_hides_storage_id():
    service = _Service()
    upload = UploadFile(
        filename="report.txt",
        file=BytesIO(b"content"),
        headers={"content-type": "text/plain"},
    )

    response = await upload_todo_artifact_file(
        todo_id="todo_1",
        request=_request(
            "/api/v1/collaboration/tasks/todos/todo_1/artifacts/object-file"
        ),
        execution_token="secret-token",
        todo_execution_id="todoexec_1",
        execution_id=None,
        collab_context_id="context_from_untrusted_legacy_client",
        artifact_name="Result",
        is_final_artifact=False,
        file=upload,
        service=service,
    )

    assert response.data.kind.value == TodoArtifactKind.FILE.value
    assert "storage_id" not in response.data.content
    assert service.calls == [
        (
            "upload",
            {
                "todo_id": "todo_1",
                "todo_execution_id": "todoexec_1",
                "execution_token": "secret-token",
                "artifact_name": "Result",
                "file_name": "report.txt",
                "content_type": "text/plain",
                "payload": b"content",
                "is_final_artifact": False,
            },
        )
    ]


@pytest.mark.asyncio
async def test_file_route_accepts_legacy_execution_id_during_compatibility_window():
    service = _Service()
    upload = UploadFile(
        filename="legacy.txt",
        file=BytesIO(b"legacy"),
        headers={"content-type": "text/plain"},
    )

    await upload_todo_artifact_file(
        todo_id="todo_1",
        request=_request(
            "/api/v1/collaboration/tasks/todos/todo_1/artifacts/object-file"
        ),
        execution_token="secret-token",
        todo_execution_id=None,
        execution_id="todoexec_legacy",
        collab_context_id="context_legacy_ignored",
        artifact_name="Legacy Result",
        is_final_artifact=True,
        file=upload,
        service=service,
    )

    assert service.calls == [
        (
            "upload",
            {
                "todo_id": "todo_1",
                "todo_execution_id": "todoexec_legacy",
                "execution_token": "secret-token",
                "artifact_name": "Legacy Result",
                "file_name": "legacy.txt",
                "content_type": "text/plain",
                "payload": b"legacy",
                "is_final_artifact": True,
            },
        )
    ]


@pytest.mark.asyncio
async def test_file_route_rejects_conflicting_execution_identifiers():
    service = _Service()
    upload = UploadFile(filename="report.txt", file=BytesIO(b"content"))

    with pytest.raises(HTTPException) as exc_info:
        await upload_todo_artifact_file(
            todo_id="todo_1",
            request=_request(
                "/api/v1/collaboration/tasks/todos/todo_1/artifacts/object-file"
            ),
            execution_token="secret-token",
            todo_execution_id="todoexec_new",
            execution_id="todoexec_old",
            collab_context_id=None,
            artifact_name="Result",
            is_final_artifact=False,
            file=upload,
            service=service,
        )

    assert exc_info.value.status_code == 422
    assert "must match" in str(exc_info.value.detail)
    assert service.calls == []
