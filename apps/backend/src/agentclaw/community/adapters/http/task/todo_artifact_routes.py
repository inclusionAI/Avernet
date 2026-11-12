"""Bot-facing Todo artifact reporting and file upload routes."""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, File, Form, Header, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from agentclaw.community.adapters.http.openapi_v1.contracts import Envelope
from agentclaw.community.adapters.http.openapi_v1.responses import envelope, envelope_errors
from agentclaw.community.adapters.http.openapi_v1.task.todos.schemas import (
    TodoArtifactRecordResponse,
)
from agentclaw.community.api.task.todo_artifact_service import (
    TodoArtifactServiceProtocol,
)
from agentclaw.community.di import Injected

router = APIRouter(
    prefix="/api/v1/collaboration/todos",
    tags=["todo-artifacts"],
)

ExecutionToken = Annotated[
    str,
    Header(
        min_length=1,
        alias="X-Todo-Execution-Token",
        description="Credential issued for the active Todo execution.",
    ),
]


class ReportTodoArtifactRequest(BaseModel):
    execution_id: str = Field(min_length=1, max_length=64)
    collab_context_id: str = Field(min_length=1, max_length=256)
    artifact_name: str = Field(min_length=1, max_length=256)
    kind: Literal["text", "url_link", "code"]
    content: dict[str, object]
    is_final_artifact: bool = False


@router.post("/{todo_id}/report", response_model=Envelope[TodoArtifactRecordResponse])
@envelope_errors
async def report_todo_artifact(
    todo_id: str,
    body: ReportTodoArtifactRequest,
    request: Request,
    execution_token: ExecutionToken,
    service: TodoArtifactServiceProtocol = Injected(TodoArtifactServiceProtocol),
) -> Envelope[TodoArtifactRecordResponse]:
    """Persist one non-file artifact reported by the active Todo Bot run."""
    record = await run_in_threadpool(
        service.report_artifact,
        todo_id=todo_id,
        collab_context_id=body.collab_context_id,
        execution_id=body.execution_id,
        execution_token=execution_token,
        artifact_name=body.artifact_name,
        artifact_kind=body.kind,
        content=body.content,
        is_final_artifact=body.is_final_artifact,
    )
    return envelope(TodoArtifactRecordResponse.from_record(record), request)


@router.post(
    "/{todo_id}/artifacts/object-file",
    response_model=Envelope[TodoArtifactRecordResponse],
)
@envelope_errors
async def upload_todo_artifact_file(
    todo_id: str,
    request: Request,
    execution_token: ExecutionToken,
    execution_id: str = Form(..., min_length=1, max_length=64),
    collab_context_id: str = Form(..., min_length=1, max_length=256),
    artifact_name: str | None = Form(default=None, max_length=256),
    is_final_artifact: bool = Form(default=False),
    file: UploadFile = File(...),
    service: TodoArtifactServiceProtocol = Injected(TodoArtifactServiceProtocol),
) -> Envelope[TodoArtifactRecordResponse]:
    """Store file bytes in the shared Task object store, then persist the Todo row."""
    payload = await file.read()
    record = await run_in_threadpool(
        service.upload_file,
        todo_id=todo_id,
        collab_context_id=collab_context_id,
        execution_id=execution_id,
        execution_token=execution_token,
        artifact_name=artifact_name,
        file_name=file.filename or "artifact.bin",
        content_type=file.content_type,
        payload=payload,
        is_final_artifact=is_final_artifact,
    )
    return envelope(TodoArtifactRecordResponse.from_record(record), request)


__all__ = ["router"]
