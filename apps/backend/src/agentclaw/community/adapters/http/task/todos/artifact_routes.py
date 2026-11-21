"""Bot-facing Todo completion, artifact reporting, and file upload routes."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field, model_validator

from agentclaw.community.adapters.http.openapi_v1.contracts import Envelope
from agentclaw.community.adapters.http.openapi_v1.responses import (
    envelope,
    envelope_errors,
)
from agentclaw.community.adapters.http.openapi_v1.task.todos.schemas import (
    TodoArtifactRecordResponse,
    TodoResponse,
)
from agentclaw.community.api.task.todo_artifact_service import (
    TodoArtifactServiceProtocol,
)
from agentclaw.community.api.task.todo_stage_service import TodoStageServiceProtocol
from agentclaw.community.core.task.todos.stage_outcome_models import TodoStageOutcomeKind
from agentclaw.community.core.task.todos.artifact_models import TodoArtifactRecord
from agentclaw.community.di import Injected

router = APIRouter(
    prefix="/api/v1/collaboration/tasks/todos",
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
    todo_execution_id: str = Field(min_length=1, max_length=64)
    execution_id: str | None = Field(default=None, min_length=1, max_length=64)
    collab_context_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=256,
        description="Deprecated and ignored; the Backend derives Context from the execution.",
    )
    artifact_name: str = Field(min_length=1, max_length=256)
    kind: Literal["text", "url_link", "code"]
    content: dict[str, object]
    is_final_artifact: bool = False

    @model_validator(mode="before")
    @classmethod
    def normalize_execution_id(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        todo_execution_id = data.get("todo_execution_id")
        legacy_execution_id = data.get("execution_id")
        if todo_execution_id and legacy_execution_id:
            if todo_execution_id != legacy_execution_id:
                raise ValueError("todo_execution_id and legacy execution_id must match")
            return data
        if legacy_execution_id:
            return {**data, "todo_execution_id": legacy_execution_id}
        return data


class CompleteTodoRequest(BaseModel):
    todo_execution_id: str = Field(min_length=1, max_length=64)
    execution_id: str | None = Field(default=None, min_length=1, max_length=64)
    collab_context_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=256,
        description="Deprecated and ignored; the Backend derives Context from the execution.",
    )

    @model_validator(mode="before")
    @classmethod
    def normalize_execution_id(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        todo_execution_id = data.get("todo_execution_id")
        legacy_execution_id = data.get("execution_id")
        if todo_execution_id and legacy_execution_id:
            if todo_execution_id != legacy_execution_id:
                raise ValueError("todo_execution_id and legacy execution_id must match")
            return data
        if legacy_execution_id:
            return {**data, "todo_execution_id": legacy_execution_id}
        return data


class CompleteTodoResponse(BaseModel):
    accepted: bool = True
    todo_id: str
    todo_execution_id: str


class ReportTodoOutcomeRequest(BaseModel):
    todo_execution_id: str = Field(min_length=1, max_length=64)
    stage_instance_id: str = Field(min_length=1, max_length=64)
    outcome: Literal["completed", "blocked", "failed"]
    summary: str = Field(min_length=1, max_length=4000)
    blocker_reason: str | None = Field(default=None, max_length=4000)
    requested_action: str | None = Field(default=None, max_length=4000)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=256)
    result: dict[str, object] = Field(default_factory=dict)


def _store_todo_artifact_file(
    service: TodoArtifactServiceProtocol,
    *,
    todo_id: str,
    todo_execution_id: str,
    execution_token: str,
    artifact_name: str | None,
    file_name: str,
    content_type: str | None,
    payload: bytes,
    is_final_artifact: bool,
) -> TodoArtifactRecord:
    """Call the deliberately synchronous Todo artifact storage contract."""
    return service.upload_file(
        todo_id=todo_id,
        todo_execution_id=todo_execution_id,
        execution_token=execution_token,
        artifact_name=artifact_name,
        file_name=file_name,
        content_type=content_type,
        payload=payload,
        is_final_artifact=is_final_artifact,
    )


def _resolve_todo_execution_id(
    *, todo_execution_id: str | None, execution_id: str | None
) -> str:
    if todo_execution_id and execution_id and todo_execution_id != execution_id:
        raise HTTPException(
            status_code=422,
            detail="todo_execution_id and legacy execution_id must match",
        )
    resolved = todo_execution_id or execution_id
    if not resolved:
        raise HTTPException(status_code=422, detail="todo_execution_id is required")
    return resolved


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
        todo_execution_id=body.todo_execution_id,
        execution_token=execution_token,
        artifact_name=body.artifact_name,
        artifact_kind=body.kind,
        content=body.content,
        is_final_artifact=body.is_final_artifact,
    )
    return envelope(TodoArtifactRecordResponse.from_record(record), request)


@router.post("/{todo_id}/outcome", response_model=Envelope[TodoResponse])
@envelope_errors
async def report_todo_outcome(
    todo_id: str,
    body: ReportTodoOutcomeRequest,
    request: Request,
    execution_token: ExecutionToken,
    service: TodoStageServiceProtocol = Injected(TodoStageServiceProtocol),
) -> Envelope[TodoResponse]:
    """Report the outcome of the active Bot stage attempt."""
    result = await run_in_threadpool(
        service.report_bot_outcome,
        todo_id=todo_id,
        stage_instance_id=body.stage_instance_id,
        todo_execution_id=body.todo_execution_id,
        execution_token=execution_token,
        outcome=TodoStageOutcomeKind(body.outcome),
        summary=body.summary,
        blocker_reason=body.blocker_reason,
        requested_action=body.requested_action,
        idempotency_key=body.idempotency_key or body.todo_execution_id,
        result=body.result,
    )
    return envelope(TodoResponse.from_record(result), request)


@router.post("/{todo_id}/complete", response_model=Envelope[CompleteTodoResponse])
@envelope_errors
async def complete_todo_execution(
    todo_id: str,
    body: CompleteTodoRequest,
    request: Request,
    execution_token: ExecutionToken,
    service: TodoArtifactServiceProtocol = Injected(TodoArtifactServiceProtocol),
) -> Envelope[CompleteTodoResponse]:
    """Mark the active Bot execution complete in its collaboration Context."""
    await run_in_threadpool(
        service.report_completion,
        todo_id=todo_id,
        todo_execution_id=body.todo_execution_id,
        execution_token=execution_token,
    )
    return envelope(
        CompleteTodoResponse(
            todo_id=todo_id,
            todo_execution_id=body.todo_execution_id,
        ),
        request,
    )


@router.post(
    "/{todo_id}/artifacts/object-file",
    response_model=Envelope[TodoArtifactRecordResponse],
)
@envelope_errors
async def upload_todo_artifact_file(
    todo_id: str,
    request: Request,
    execution_token: ExecutionToken,
    todo_execution_id: str | None = Form(default=None, min_length=1, max_length=64),
    execution_id: str | None = Form(default=None, min_length=1, max_length=64),
    collab_context_id: str | None = Form(
        default=None,
        min_length=1,
        max_length=256,
        description="Deprecated and ignored.",
    ),
    artifact_name: str | None = Form(default=None, max_length=256),
    is_final_artifact: bool = Form(default=False),
    file: UploadFile = File(...),
    service: TodoArtifactServiceProtocol = Injected(TodoArtifactServiceProtocol),
) -> Envelope[TodoArtifactRecordResponse]:
    """Store file bytes in the shared Task object store, then persist the Todo row."""
    resolved_execution_id = _resolve_todo_execution_id(
        todo_execution_id=todo_execution_id,
        execution_id=execution_id,
    )
    payload = await file.read()
    record = await run_in_threadpool(
        _store_todo_artifact_file,
        service,
        todo_id=todo_id,
        todo_execution_id=resolved_execution_id,
        execution_token=execution_token,
        artifact_name=artifact_name,
        file_name=file.filename or "artifact.bin",
        content_type=file.content_type,
        payload=payload,
        is_final_artifact=is_final_artifact,
    )
    return envelope(TodoArtifactRecordResponse.from_record(record), request)


__all__ = ["router"]
