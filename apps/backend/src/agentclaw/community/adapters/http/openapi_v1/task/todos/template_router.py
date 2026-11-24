"""Human-only HTTP management surface for versioned Todo templates."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Request, Response
from fastapi.concurrency import run_in_threadpool

from agentclaw.community.adapters.http.openapi_v1.authorization import PublicAPIRoute
from agentclaw.community.adapters.http.openapi_v1.contracts import (
    USER_SCOPED_ERROR_RESPONSES,
    Envelope,
    Page,
    PageParamsDep,
)
from agentclaw.community.adapters.http.openapi_v1.principal import (
    UserIdDep,
    refuse_app_only_caller,
)
from agentclaw.community.adapters.http.openapi_v1.responses import (
    created,
    envelope,
    envelope_errors,
    page as page_envelope,
)
from agentclaw.community.api.task.todo_template_service import (
    TodoTemplateManagementServiceProtocol,
)
from agentclaw.community.core.task.todos.template_management_models import (
    TodoTemplateStatus,
)
from agentclaw.community.di import Injected

from .template_schemas import (
    CreateTodoTemplateRequest,
    TodoTemplateDetailResponse,
    TodoTemplateRecordResponse,
    TodoTemplateVersionResponse,
    UpdateTodoTemplateDraftRequest,
)

TemplateIdPath = Annotated[
    str,
    Path(
        min_length=1,
        max_length=64,
        description="Stable Todo template identifier.",
    ),
]
VersionIdPath = Annotated[
    str,
    Path(
        min_length=1,
        max_length=64,
        description="Stable Todo template version identifier.",
    ),
]

router = APIRouter(
    prefix="/openapi/v1/collaboration/tasks/todo-templates",
    tags=["todo-templates"],
    route_class=PublicAPIRoute,
    dependencies=[Depends(refuse_app_only_caller)],
)


@router.post(
    "",
    response_model=Envelope[TodoTemplateDetailResponse],
    responses=USER_SCOPED_ERROR_RESPONSES,
)
@envelope_errors
async def create_todo_template(
    body: CreateTodoTemplateRequest,
    request: Request,
    response: Response,
    user_id: UserIdDep,
    service: TodoTemplateManagementServiceProtocol = Injected(
        TodoTemplateManagementServiceProtocol
    ),
) -> Envelope[TodoTemplateDetailResponse]:
    """Create a template identity and its first draft as a Space Admin."""
    detail = await run_in_threadpool(  # allow-run-in-threadpool: TodoTemplateManagementServiceProtocol method is synchronous.
        service.create_template,
        space_id=body.space_id,
        actor_id=user_id,
        definition=body.definition.to_domain(),
    )
    response.status_code = 201
    return created(TodoTemplateDetailResponse.from_detail(detail), request)


@router.get(
    "",
    response_model=Envelope[Page[TodoTemplateRecordResponse]],
    responses=USER_SCOPED_ERROR_RESPONSES,
)
@envelope_errors
async def list_todo_templates(
    request: Request,
    page_params: PageParamsDep,
    user_id: UserIdDep,
    space_id: int = Query(
        gt=0,
        description="Product Space whose Todo templates are listed.",
    ),
    status: TodoTemplateStatus | None = Query(
        default=None,
        description="Optional template lifecycle status filter.",
    ),
    service: TodoTemplateManagementServiceProtocol = Injected(
        TodoTemplateManagementServiceProtocol
    ),
) -> Envelope[Page[TodoTemplateRecordResponse]]:
    """List Todo templates visible to a Space member."""
    result = await run_in_threadpool(  # allow-run-in-threadpool: TodoTemplateManagementServiceProtocol method is synchronous.
        service.list_templates,
        space_id=space_id,
        actor_id=user_id,
        status=status,
        page=page_params.page,
        page_size=page_params.page_size,
    )
    return page_envelope(
        result.total,
        [TodoTemplateRecordResponse.from_record(item) for item in result.items],
        request,
    )


@router.get(
    "/{template_id}",
    response_model=Envelope[TodoTemplateDetailResponse],
    responses=USER_SCOPED_ERROR_RESPONSES,
)
@envelope_errors
async def get_todo_template(
    template_id: TemplateIdPath,
    request: Request,
    user_id: UserIdDep,
    service: TodoTemplateManagementServiceProtocol = Injected(
        TodoTemplateManagementServiceProtocol
    ),
) -> Envelope[TodoTemplateDetailResponse]:
    """Read template metadata and ordered versions as a Space member."""
    result = await run_in_threadpool(  # allow-run-in-threadpool: TodoTemplateManagementServiceProtocol method is synchronous.
        service.get_template,
        template_id=template_id,
        actor_id=user_id,
    )
    return envelope(TodoTemplateDetailResponse.from_detail(result), request)


@router.get(
    "/{template_id}/versions/{version_id}",
    response_model=Envelope[TodoTemplateVersionResponse],
    responses=USER_SCOPED_ERROR_RESPONSES,
)
@envelope_errors
async def get_todo_template_version(
    template_id: TemplateIdPath,
    version_id: VersionIdPath,
    request: Request,
    user_id: UserIdDep,
    service: TodoTemplateManagementServiceProtocol = Injected(
        TodoTemplateManagementServiceProtocol
    ),
) -> Envelope[TodoTemplateVersionResponse]:
    """Read one exact draft or published template version."""
    result = await run_in_threadpool(
        service.get_version,
        template_id=template_id,
        template_version_id=version_id,
        actor_id=user_id,
    )
    return envelope(TodoTemplateVersionResponse.from_record(result), request)


@router.post(
    "/{template_id}/versions",
    response_model=Envelope[TodoTemplateVersionResponse],
    responses=USER_SCOPED_ERROR_RESPONSES,
)
@envelope_errors
async def create_todo_template_draft(
    template_id: TemplateIdPath,
    request: Request,
    response: Response,
    user_id: UserIdDep,
    service: TodoTemplateManagementServiceProtocol = Injected(
        TodoTemplateManagementServiceProtocol
    ),
) -> Envelope[TodoTemplateVersionResponse]:
    """Create the next editable draft by copying the latest version."""
    result = await run_in_threadpool(
        service.create_draft,
        template_id=template_id,
        actor_id=user_id,
    )
    response.status_code = 201
    return created(TodoTemplateVersionResponse.from_record(result), request)


@router.put(
    "/{template_id}/versions/{version_id}",
    response_model=Envelope[TodoTemplateVersionResponse],
    responses=USER_SCOPED_ERROR_RESPONSES,
)
@envelope_errors
async def update_todo_template_draft(
    template_id: TemplateIdPath,
    version_id: VersionIdPath,
    body: UpdateTodoTemplateDraftRequest,
    request: Request,
    user_id: UserIdDep,
    service: TodoTemplateManagementServiceProtocol = Injected(
        TodoTemplateManagementServiceProtocol
    ),
) -> Envelope[TodoTemplateVersionResponse]:
    """Replace one editable draft definition as a Space Admin."""
    result = await run_in_threadpool(
        service.update_draft,
        template_id=template_id,
        template_version_id=version_id,
        actor_id=user_id,
        definition=body.definition.to_domain(),
    )
    return envelope(TodoTemplateVersionResponse.from_record(result), request)


@router.post(
    "/{template_id}/versions/{version_id}/validate",
    response_model=Envelope[TodoTemplateVersionResponse],
    responses=USER_SCOPED_ERROR_RESPONSES,
)
@envelope_errors
async def validate_todo_template_version(
    template_id: TemplateIdPath,
    version_id: VersionIdPath,
    request: Request,
    user_id: UserIdDep,
    service: TodoTemplateManagementServiceProtocol = Injected(
        TodoTemplateManagementServiceProtocol
    ),
) -> Envelope[TodoTemplateVersionResponse]:
    """Validate a stored version against current deterministic capabilities."""
    result = await run_in_threadpool(
        service.validate_version,
        template_id=template_id,
        template_version_id=version_id,
        actor_id=user_id,
    )
    return envelope(TodoTemplateVersionResponse.from_record(result), request)


@router.post(
    "/{template_id}/versions/{version_id}/publish",
    response_model=Envelope[TodoTemplateVersionResponse],
    responses=USER_SCOPED_ERROR_RESPONSES,
)
@envelope_errors
async def publish_todo_template_version(
    template_id: TemplateIdPath,
    version_id: VersionIdPath,
    request: Request,
    user_id: UserIdDep,
    service: TodoTemplateManagementServiceProtocol = Injected(
        TodoTemplateManagementServiceProtocol
    ),
) -> Envelope[TodoTemplateVersionResponse]:
    """Publish one validated draft as an immutable template version."""
    result = await run_in_threadpool(
        service.publish_version,
        template_id=template_id,
        template_version_id=version_id,
        actor_id=user_id,
    )
    return envelope(TodoTemplateVersionResponse.from_record(result), request)


@router.post(
    "/{template_id}/archive",
    response_model=Envelope[TodoTemplateRecordResponse],
    responses=USER_SCOPED_ERROR_RESPONSES,
)
@envelope_errors
async def archive_todo_template(
    template_id: TemplateIdPath,
    request: Request,
    user_id: UserIdDep,
    service: TodoTemplateManagementServiceProtocol = Injected(
        TodoTemplateManagementServiceProtocol
    ),
) -> Envelope[TodoTemplateRecordResponse]:
    """Prevent a template from receiving new drafts or Todo creations."""
    result = await run_in_threadpool(
        service.archive_template,
        template_id=template_id,
        actor_id=user_id,
    )
    return envelope(TodoTemplateRecordResponse.from_record(result), request)


@router.post(
    "/{template_id}/activate",
    response_model=Envelope[TodoTemplateRecordResponse],
    responses=USER_SCOPED_ERROR_RESPONSES,
)
@envelope_errors
async def activate_todo_template(
    template_id: TemplateIdPath,
    request: Request,
    user_id: UserIdDep,
    service: TodoTemplateManagementServiceProtocol = Injected(
        TodoTemplateManagementServiceProtocol
    ),
) -> Envelope[TodoTemplateRecordResponse]:
    """Reactivate a previously archived Todo template."""
    result = await run_in_threadpool(
        service.activate_template,
        template_id=template_id,
        actor_id=user_id,
    )
    return envelope(TodoTemplateRecordResponse.from_record(result), request)
