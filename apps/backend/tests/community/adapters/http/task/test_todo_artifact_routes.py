from __future__ import annotations

from datetime import datetime, timezone
from io import BytesIO
from typing import Any

import pytest
from fastapi import Request, UploadFile

from agentclaw.community.adapters.http.task.todo_artifact_routes import (
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
            execution_id="todoexec_1",
            collab_context_id="context_1",
            artifact_name="Result",
            kind="text",
            content={"text": "done"},
            is_final_artifact=True,
        ),
        request=_request("/api/v1/collaboration/todos/todo_1/report"),
        execution_token="secret-token",
        service=service,
    )

    assert response.request_id == "trace-todo-artifact"
    assert response.data.kind is TodoArtifactKind.TEXT
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
                "collab_context_id": "context_1",
                "execution_id": "todoexec_1",
                "execution_token": "secret-token",
                "artifact_name": "Result",
                "artifact_kind": "text",
                "content": {"text": "done"},
                "is_final_artifact": True,
            },
        )
    ]


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
            "/api/v1/collaboration/todos/todo_1/artifacts/object-file"
        ),
        execution_token="secret-token",
        execution_id="todoexec_1",
        collab_context_id="context_1",
        artifact_name="Result",
        is_final_artifact=False,
        file=upload,
        service=service,
    )

    assert response.data.kind is TodoArtifactKind.FILE
    assert "storage_id" not in response.data.content
    assert service.calls == [
        (
            "upload",
            {
                "todo_id": "todo_1",
                "collab_context_id": "context_1",
                "execution_id": "todoexec_1",
                "execution_token": "secret-token",
                "artifact_name": "Result",
                "file_name": "report.txt",
                "content_type": "text/plain",
                "payload": b"content",
                "is_final_artifact": False,
            },
        )
    ]
