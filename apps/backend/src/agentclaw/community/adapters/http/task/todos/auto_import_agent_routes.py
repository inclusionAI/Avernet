"""Authenticated internal routes for external automatic Dima Todo imports."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Request, Response
from fastapi.concurrency import run_in_threadpool

from agentclaw.community.adapters.http.auth.dependencies import get_current_user
from agentclaw.community.adapters.http.openapi_v1.contracts import Envelope
from agentclaw.community.adapters.http.openapi_v1.responses import (
    created,
    envelope,
    envelope_errors,
)
from agentclaw.community.api.task.todo_auto_import_service import (
    TodoAutoImportServiceProtocol,
)
from agentclaw.community.core.task.todos.auto_import_models import (
    TodoAutoImportCheckpoint,
    TodoAutoImportProgressStatus,
)
from agentclaw.community.core.task.todos.source_models import TodoSourceInput
from agentclaw.community.di import Injected

from ...openapi_v1.task.todos.schemas import (
    CreateTodoFromAutoImportRuleRequest,
    TodoAutoImportCheckpointRequest,
    TodoAutoImportProgressResponse,
    TodoAutoImportRuleResponse,
    TodoResponse,
)

router = APIRouter(
    prefix="/api/v1/collaboration/tasks/todos",
    tags=["todos"],
    dependencies=[Depends(get_current_user)],
)


@router.get(
    "/sources/dima/auto-import-rules/active",
    response_model=Envelope[list[TodoAutoImportRuleResponse]],
)
@envelope_errors
async def list_active_dima_auto_import_rules(
    request: Request,
    space_id: int | None = Query(
        default=None,
        gt=0,
        description="Only return enabled rules for this Product Space.",
    ),
    service: TodoAutoImportServiceProtocol = Injected(TodoAutoImportServiceProtocol),
) -> Envelope[list[TodoAutoImportRuleResponse]]:
    """List enabled rules visible to the authenticated tenant caller."""
    rules = await run_in_threadpool(
        service.list_active_rules,
        space_id=space_id,
    )
    return envelope(
        [TodoAutoImportRuleResponse.from_record(rule) for rule in rules], request
    )


@router.get(
    "/sources/dima/auto-import-rules/{rule_id}",
    response_model=Envelope[TodoAutoImportRuleResponse],
)
@envelope_errors
async def get_dima_auto_import_rule(
    rule_id: Annotated[
        str,
        Path(
            min_length=1,
            max_length=64,
            description="Automatic Dima import rule identifier.",
        ),
    ],
    request: Request,
    service: TodoAutoImportServiceProtocol = Injected(TodoAutoImportServiceProtocol),
) -> Envelope[TodoAutoImportRuleResponse]:
    """Return one rule visible to the authenticated tenant caller."""
    rule = await run_in_threadpool(service.get_rule, rule_id=rule_id)
    return envelope(TodoAutoImportRuleResponse.from_record(rule), request)


@router.get(
    "/sources/dima/auto-import-rules/{rule_id}/progress",
    response_model=Envelope[list[TodoAutoImportProgressResponse]],
)
@envelope_errors
async def list_dima_auto_import_progress(
    rule_id: Annotated[
        str,
        Path(
            min_length=1,
            max_length=64,
            description="Automatic Dima import rule identifier.",
        ),
    ],
    request: Request,
    source_container_id: str | None = Query(
        default=None,
        min_length=1,
        max_length=128,
        description="Only return checkpoints from this Dima workspace or project.",
    ),
    source_item_id: str | None = Query(
        default=None,
        min_length=1,
        max_length=128,
        description="Only return checkpoints for this Dima work item.",
    ),
    source_updated_at: datetime | None = Query(
        default=None,
        description="Only return the checkpoint for this exact source update time.",
    ),
    limit: int = Query(
        default=100,
        ge=1,
        le=500,
        description="Maximum number of checkpoint records to return.",
    ),
    service: TodoAutoImportServiceProtocol = Injected(TodoAutoImportServiceProtocol),
) -> Envelope[list[TodoAutoImportProgressResponse]]:
    """List retry-safe processing outcomes for one automatic import rule."""
    progress = await run_in_threadpool(
        service.list_progress,
        rule_id=rule_id,
        source_container_id=source_container_id,
        source_item_id=source_item_id,
        source_updated_at=source_updated_at,
        limit=limit,
    )
    return envelope(
        [TodoAutoImportProgressResponse.from_record(item) for item in progress],
        request,
    )


@router.post(
    "/sources/dima/auto-import-rules/{rule_id}/checkpoints",
    response_model=Envelope[TodoAutoImportProgressResponse],
)
@envelope_errors
async def record_dima_auto_import_checkpoint(
    rule_id: Annotated[
        str,
        Path(
            min_length=1,
            max_length=64,
            description="Automatic Dima import rule identifier.",
        ),
    ],
    body: TodoAutoImportCheckpointRequest,
    request: Request,
    service: TodoAutoImportServiceProtocol = Injected(TodoAutoImportServiceProtocol),
) -> Envelope[TodoAutoImportProgressResponse]:
    """Persist one Agent outcome and optionally advance its monotonic cursor."""
    progress = await run_in_threadpool(
        service.record_checkpoint,
        rule_id=rule_id,
        checkpoint=TodoAutoImportCheckpoint(
            source_container_id=body.source_container_id,
            source_item_id=body.source_item_id,
            source_updated_at=body.source_updated_at,
            status=TodoAutoImportProgressStatus(body.status),
            todo_id=body.todo_id,
            detail=body.detail,
            cursor_updated_at=body.cursor_updated_at,
        ),
    )
    return envelope(TodoAutoImportProgressResponse.from_record(progress), request)


@router.post(
    "/sources/dima/auto-import-rules/{rule_id}/todos",
    response_model=Envelope[TodoResponse],
)
@envelope_errors
async def create_todo_from_dima_auto_import_rule(
    rule_id: Annotated[
        str,
        Path(
            min_length=1,
            max_length=64,
            description="Automatic Dima import rule identifier.",
        ),
    ],
    body: CreateTodoFromAutoImportRuleRequest,
    request: Request,
    response: Response,
    service: TodoAutoImportServiceProtocol = Injected(TodoAutoImportServiceProtocol),
) -> Envelope[TodoResponse]:
    """Create a Todo with ownership, Space, and Bot fixed by the rule."""
    todo = await run_in_threadpool(
        service.create_todo_from_rule,
        rule_id=rule_id,
        title=body.title,
        description=body.description,
        source=TodoSourceInput(
            source_type=body.source.type,
            container_id=body.source.container_id,
            item_id=body.source.item_id,
            url=body.source.url,
            updated_at=body.source.updated_at,
            metadata=body.source.metadata,
        ),
    )
    response.status_code = 201
    return created(TodoResponse.from_record(todo), request)
