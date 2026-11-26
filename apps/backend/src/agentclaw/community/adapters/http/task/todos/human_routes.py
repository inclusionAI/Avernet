"""Authenticated internal Todo workbench routes served without the public gateway."""

from __future__ import annotations

from typing import Annotated, Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, Path, Query, Request, Response
from fastapi.concurrency import run_in_threadpool

from agentclaw.community.adapters.http.auth.dependencies import get_current_user
from agentclaw.community.adapters.http.auth.models import AuthenticatedUser
from agentclaw.community.adapters.http.openapi_v1.contracts import (
    Envelope,
    Page,
    PageParamsDep,
)

from agentclaw.community.adapters.http.openapi_v1.errors import UserIdMismatchError
from agentclaw.community.adapters.http.openapi_v1.principal import USER_ID_DESCRIPTION
from agentclaw.community.adapters.http.openapi_v1.responses import (
    created,
    envelope,
    envelope_errors,
)
from agentclaw.community.adapters.http.openapi_v1.responses import (
    page as page_envelope,
)
from agentclaw.community.api.task.todo_artifact_service import (
    TodoArtifactServiceProtocol,
)
from agentclaw.community.api.task.todo_auto_import_service import (
    TodoAutoImportServiceProtocol,
)
from agentclaw.community.api.task.todo_service import TodoServiceProtocol
from agentclaw.community.api.task.todo_source_service import TodoSourceServiceProtocol
from agentclaw.community.api.task.todo_stage_service import TodoStageServiceProtocol
from agentclaw.community.api.task.todo_template_creation_service import (
    TodoTemplateCreationServiceProtocol,
)
from agentclaw.community.core.task.todos.artifact_models import TodoArtifactDownload
from agentclaw.community.core.task.todos.models import (
    MAX_BOT_ID_LENGTH,
    MAX_TITLE_LENGTH,
    TodoAcceptanceTarget,
    TodoAcceptanceTargetKind,
    TodoInteractionMention,
    TodoInteractionMentionActorType,
)
from agentclaw.community.core.task.todos.source_models import TodoSourceInput
from agentclaw.community.core.task.todos.stage_models import TodoParticipantBindingInput
from agentclaw.community.di import Injected
from agentclaw.community.plugin_api.auth import AuthRequestContext

from ...openapi_v1.task.todos.schemas import (
    CancelTodoRequest,
    CompleteTodoStageRequest,
    CreateTodoAutoImportRuleRequest,
    CreateTodoFromTemplateRequest,
    CreateTodoInteractionRequest,
    CreateTodoRequest,
    SubmitTodoAcceptanceRequest,
    TodoArtifactRecordResponse,
    TodoAutoImportRuleResponse,
    TodoEventResponse,
    TodoInteractionResponse,
    TodoResponse,
    TodoSourceDraftResponse,
    TodoSourceProjectResponse,
    TodoSourceWorkItemResponse,
    TodoStageResponse,
    TodoStatusSchema,
    UpdateTodoAutoImportRuleRequest,
)


def _load_todo_artifact_file(
    service: TodoArtifactServiceProtocol,
    *,
    todo_id: str,
    artifact_id: str,
    actor_id: str,
) -> TodoArtifactDownload:
    """Call the deliberately synchronous Todo artifact read contract."""
    return service.read_file(
        todo_id=todo_id,
        artifact_id=artifact_id,
        actor_id=actor_id,
    )


def _auth_request(request: Request, user: AuthenticatedUser) -> AuthRequestContext:
    return AuthRequestContext(
        cookies=dict(request.cookies),
        headers=dict(request.headers),
        query_params=dict(request.query_params),
        base_url=str(request.base_url),
        verified_actor_user_id=user.staffId,
    )


def call_user(*, user_id: str, user: AuthenticatedUser) -> str:
    """Confirm the named owner is the logged-in Human and return her staff id."""
    if user_id != user.staffId:
        raise UserIdMismatchError(
            f"named user_id does not match authenticated staff {user.staffId}"
        )
    return user.staffId


router = APIRouter(
    prefix="/api/v1/collaboration/tasks/todos",
    tags=["todos"],
)

AuthenticatedUserDep = Annotated[AuthenticatedUser, Depends(get_current_user)]


@router.get("", response_model=Envelope[Page[TodoResponse]])
@envelope_errors
async def list_todos(
    request: Request,
    page_params: PageParamsDep,
    user: AuthenticatedUserDep,
    user_id: str = Query(min_length=1, description=USER_ID_DESCRIPTION),
    space_id: int = Query(
        gt=0,
        description="Product Space whose Todos are listed.",
    ),
    keyword: str | None = Query(
        default=None,
        max_length=MAX_TITLE_LENGTH,
        description=(
            "Case-insensitive text matched against title, description, and Bot name."
        ),
    ),
    status: TodoStatusSchema | None = Query(
        default=None,
        description="Only return Todos in this A-owned product state.",
    ),
    bot_id: str | None = Query(
        default=None,
        max_length=MAX_BOT_ID_LENGTH,
        description="Only return Todos assigned to this Bot.",
    ),
    service: TodoServiceProtocol = Injected(TodoServiceProtocol),
) -> Envelope[Page[TodoResponse]]:
    """List the caller's Todos in the selected product Space."""
    actor_id = call_user(user_id=user_id, user=user)
    result = await run_in_threadpool(  # allow-run-in-threadpool
        service.list_todos,
        space_id=space_id,
        actor_id=actor_id,
        keyword=keyword,
        status=status,
        bot_id=bot_id,
        page=page_params.page,
        page_size=page_params.page_size,
    )
    return page_envelope(
        result.total,
        [TodoResponse.from_record(item) for item in result.items],
        request,
    )


@router.post("", response_model=Envelope[TodoResponse])
@envelope_errors
async def create_todo(
    body: CreateTodoRequest,
    request: Request,
    response: Response,
    user: AuthenticatedUserDep,
    user_id: str = Query(min_length=1, description=USER_ID_DESCRIPTION),
    service: TodoServiceProtocol = Injected(TodoServiceProtocol),
) -> Envelope[TodoResponse]:
    """Create one pending Todo for a Space-eligible Bot and await dispatch."""
    actor_id = call_user(user_id=user_id, user=user)
    create_args: dict[str, object] = {
        "space_id": body.space_id,
        "actor_id": actor_id,
        "title": body.title,
        "description": body.description,
        "bot_id": body.bot_id,
    }
    if body.source is not None:
        create_args["source"] = TodoSourceInput(
            source_type=body.source.type,
            container_id=body.source.container_id,
            item_id=body.source.item_id,
            url=body.source.url,
            updated_at=body.source.updated_at,
            metadata=body.source.metadata,
        )
    result = await run_in_threadpool(  # allow-run-in-threadpool
        service.create_todo,
        **create_args,
    )
    response.status_code = 201
    return created(TodoResponse.from_record(result), request)


@router.get(
    "/sources/dima/projects",
    response_model=Envelope[list[TodoSourceProjectResponse]],
)
@envelope_errors
async def list_dima_projects(
    request: Request,
    user: AuthenticatedUserDep,
    user_id: str = Query(min_length=1, description=USER_ID_DESCRIPTION),
    service: TodoSourceServiceProtocol = Injected(TodoSourceServiceProtocol),
) -> Envelope[list[TodoSourceProjectResponse]]:
    """List Dima projects visible through the authenticated Human identity."""
    actor_id = call_user(user_id=user_id, user=user)
    projects = await run_in_threadpool(
        service.list_projects,
        actor_id=actor_id,
        auth_request=_auth_request(request, user),
    )
    return envelope(
        [TodoSourceProjectResponse.from_record(item) for item in projects], request
    )


@router.get(
    "/sources/dima/projects/{project_id}/work-items",
    response_model=Envelope[list[TodoSourceWorkItemResponse]],
)
@envelope_errors
async def list_dima_work_items(
    project_id: Annotated[
        str,
        Path(
            min_length=1,
            max_length=128,
            description="Dima project identifier selected by the authenticated Human.",
        ),
    ],
    request: Request,
    user: AuthenticatedUserDep,
    user_id: str = Query(min_length=1, description=USER_ID_DESCRIPTION),
    query: str | None = Query(
        default=None,
        max_length=MAX_TITLE_LENGTH,
        description="Optional case-insensitive Dima work-item id or title filter.",
    ),
    service: TodoSourceServiceProtocol = Injected(TodoSourceServiceProtocol),
) -> Envelope[list[TodoSourceWorkItemResponse]]:
    """List normalized Dima work items in one selected project."""
    actor_id = call_user(user_id=user_id, user=user)
    items = await run_in_threadpool(
        service.list_work_items,
        actor_id=actor_id,
        auth_request=_auth_request(request, user),
        project_id=project_id,
        query=query,
    )
    return envelope(
        [TodoSourceWorkItemResponse.from_record(item) for item in items], request
    )


@router.get(
    "/sources/dima/projects/{project_id}/work-items/{work_item_id}",
    response_model=Envelope[TodoSourceDraftResponse],
)
@envelope_errors
async def get_dima_work_item_draft(
    project_id: Annotated[
        str,
        Path(
            min_length=1,
            max_length=128,
            description="Dima project identifier containing the selected work item.",
        ),
    ],
    work_item_id: Annotated[
        str,
        Path(
            min_length=1,
            max_length=128,
            description="Dima Req, Task, or Bug identifier to normalize as a Todo draft.",
        ),
    ],
    request: Request,
    user: AuthenticatedUserDep,
    user_id: str = Query(min_length=1, description=USER_ID_DESCRIPTION),
    service: TodoSourceServiceProtocol = Injected(TodoSourceServiceProtocol),
) -> Envelope[TodoSourceDraftResponse]:
    """Load one Dima work item as editable Todo fields."""
    actor_id = call_user(user_id=user_id, user=user)
    draft = await run_in_threadpool(
        service.load_work_item_draft,
        actor_id=actor_id,
        auth_request=_auth_request(request, user),
        project_id=project_id,
        work_item_id=work_item_id,
    )
    return envelope(TodoSourceDraftResponse.from_record(draft), request)


@router.post(
    "/sources/dima/auto-import-rules",
    response_model=Envelope[TodoAutoImportRuleResponse],
)
@envelope_errors
async def create_dima_auto_import_rule(
    body: CreateTodoAutoImportRuleRequest,
    request: Request,
    response: Response,
    user: AuthenticatedUserDep,
    user_id: str = Query(min_length=1, description=USER_ID_DESCRIPTION),
    service: TodoAutoImportServiceProtocol = Injected(TodoAutoImportServiceProtocol),
) -> Envelope[TodoAutoImportRuleResponse]:
    """Save one Dima auto-import rule for an external Agent to execute."""
    actor_id = call_user(user_id=user_id, user=user)
    rule = await run_in_threadpool(
        service.create_rule,
        space_id=body.space_id,
        actor_id=actor_id,
        source_container_ids=tuple(body.source_container_ids),
        filter_text=body.filter_text,
        bot_id=body.bot_id,
    )
    response.status_code = 201
    return created(TodoAutoImportRuleResponse.from_record(rule), request)


@router.get(
    "/sources/dima/auto-import-rules",
    response_model=Envelope[list[TodoAutoImportRuleResponse]],
)
@envelope_errors
async def list_dima_auto_import_rules(
    request: Request,
    user: AuthenticatedUserDep,
    user_id: str = Query(min_length=1, description=USER_ID_DESCRIPTION),
    space_id: int | None = Query(
        default=None,
        gt=0,
        description="Only return rules owned by this Product Space.",
    ),
    status: Literal["enabled", "disabled"] | None = Query(
        default=None,
        description="Only return rules in this enablement state.",
    ),
    service: TodoAutoImportServiceProtocol = Injected(TodoAutoImportServiceProtocol),
) -> Envelope[list[TodoAutoImportRuleResponse]]:
    """List the authenticated Human's automatic Dima import rules."""
    actor_id = call_user(user_id=user_id, user=user)
    rules = await run_in_threadpool(
        service.list_rules,
        actor_id=actor_id,
        space_id=space_id,
        status=status,
    )
    return envelope(
        [TodoAutoImportRuleResponse.from_record(rule) for rule in rules], request
    )


@router.patch(
    "/sources/dima/auto-import-rules/{rule_id}",
    response_model=Envelope[TodoAutoImportRuleResponse],
)
@envelope_errors
async def update_dima_auto_import_rule(
    rule_id: Annotated[
        str,
        Path(
            min_length=1,
            max_length=64,
            description="Automatic Dima import rule identifier.",
        ),
    ],
    body: UpdateTodoAutoImportRuleRequest,
    request: Request,
    user: AuthenticatedUserDep,
    user_id: str = Query(min_length=1, description=USER_ID_DESCRIPTION),
    service: TodoAutoImportServiceProtocol = Injected(TodoAutoImportServiceProtocol),
) -> Envelope[TodoAutoImportRuleResponse]:
    """Update configuration or enablement for one automatic import rule."""
    actor_id = call_user(user_id=user_id, user=user)
    rule = await run_in_threadpool(
        service.update_rule,
        rule_id=rule_id,
        actor_id=actor_id,
        source_container_ids=(
            tuple(body.source_container_ids)
            if body.source_container_ids is not None
            else None
        ),
        filter_text=body.filter_text,
        bot_id=body.bot_id,
        status=body.status,
    )
    return envelope(TodoAutoImportRuleResponse.from_record(rule), request)


@router.post("/from-template", response_model=Envelope[TodoResponse])
@envelope_errors
async def create_todo_from_template(
    body: CreateTodoFromTemplateRequest,
    request: Request,
    response: Response,
    user: AuthenticatedUserDep,
    user_id: str = Query(min_length=1, description=USER_ID_DESCRIPTION),
    space_id: int = Query(gt=0, description="Product Space that owns the Todo."),
    service: TodoTemplateCreationServiceProtocol = Injected(
        TodoTemplateCreationServiceProtocol
    ),
) -> Envelope[TodoResponse]:
    """Create and expand one Todo from a published product-flow template."""
    actor_id = call_user(user_id=user_id, user=user)
    bindings = {
        key: TodoParticipantBindingInput(
            subject_type=value.subject_type,
            subject_id=value.subject_id,
            subject_name=value.subject_name,
        )
        for key, value in body.participant_bindings.items()
    }
    result = await run_in_threadpool(  # allow-run-in-threadpool: TodoTemplateCreationServiceProtocol method is synchronous.
        service.create_todo_from_template,
        space_id=space_id,
        actor_id=actor_id,
        template_id=body.template_id,
        template_version_id=body.template_version_id,
        title=body.title,
        description=body.description,
        bindings=bindings,
    )
    response.status_code = 201
    return created(TodoResponse.from_record(result), request)


@router.get("/{todo_id}", response_model=Envelope[TodoResponse])
@envelope_errors
async def get_todo(
    todo_id: Annotated[str, Path(description="A-owned Todo identifier.")],
    request: Request,
    user: AuthenticatedUserDep,
    user_id: str = Query(min_length=1, description=USER_ID_DESCRIPTION),
    service: TodoServiceProtocol = Injected(TodoServiceProtocol),
) -> Envelope[TodoResponse]:
    """Return one Todo visible to the authenticated Human."""
    actor_id = call_user(user_id=user_id, user=user)
    result = await run_in_threadpool(  # allow-run-in-threadpool
        service.get_todo,
        todo_id=todo_id,
        actor_id=actor_id,
    )
    return envelope(TodoResponse.from_record(result), request)


@router.post("/{todo_id}/run", response_model=Envelope[TodoResponse])
@envelope_errors
async def run_todo(
    todo_id: Annotated[str, Path(description="A-owned Todo identifier.")],
    request: Request,
    user: AuthenticatedUserDep,
    user_id: str = Query(min_length=1, description=USER_ID_DESCRIPTION),
    service: TodoServiceProtocol = Injected(TodoServiceProtocol),
) -> Envelope[TodoResponse]:
    """Compensate automatic dispatch by running one still-pending Todo."""
    actor_id = call_user(user_id=user_id, user=user)
    result = await service.start_todo(
        todo_id=todo_id,
        actor_id=actor_id,
    )
    return envelope(TodoResponse.from_record(result), request)


@router.get("/{todo_id}/stages", response_model=Envelope[list[TodoStageResponse]])
@envelope_errors
async def list_todo_stages(
    todo_id: Annotated[str, Path(description="A-owned Todo identifier.")],
    request: Request,
    user: AuthenticatedUserDep,
    user_id: str = Query(min_length=1, description=USER_ID_DESCRIPTION),
    service: TodoServiceProtocol = Injected(TodoServiceProtocol),
) -> Envelope[list[TodoStageResponse]]:
    """Return the immutable expanded stage timeline for one Todo."""
    actor_id = call_user(user_id=user_id, user=user)
    stages = await run_in_threadpool(
        service.list_stages,
        todo_id=todo_id,
        actor_id=actor_id,
    )
    return envelope([TodoStageResponse.from_record(item) for item in stages], request)


@router.post(
    "/{todo_id}/stages/{stage_instance_id}/complete",
    response_model=Envelope[TodoResponse],
)
@envelope_errors
async def complete_human_todo_stage(
    todo_id: Annotated[str, Path(description="A-owned Todo identifier.")],
    stage_instance_id: Annotated[
        str, Path(description="Current Human stage instance.")
    ],
    body: CompleteTodoStageRequest,
    request: Request,
    user: AuthenticatedUserDep,
    user_id: str = Query(min_length=1, description=USER_ID_DESCRIPTION),
    service: TodoStageServiceProtocol = Injected(TodoStageServiceProtocol),
) -> Envelope[TodoResponse]:
    """Complete the current Human stage as its owner or a Space admin."""
    actor_id = call_user(user_id=user_id, user=user)
    result = await run_in_threadpool(
        service.complete_human_stage,
        todo_id=todo_id,
        stage_instance_id=stage_instance_id,
        actor_id=actor_id,
        summary=body.summary,
        idempotency_key=body.idempotency_key,
        result=body.result,
    )
    return envelope(TodoResponse.from_record(result), request)


@router.post("/{todo_id}/resume", response_model=Envelope[TodoResponse])
@envelope_errors
async def resume_todo(
    todo_id: Annotated[str, Path(description="A-owned Todo identifier.")],
    request: Request,
    user: AuthenticatedUserDep,
    user_id: str = Query(min_length=1, description=USER_ID_DESCRIPTION),
    service: TodoStageServiceProtocol = Injected(TodoStageServiceProtocol),
) -> Envelope[TodoResponse]:
    """Resume an interrupted or blocked Todo."""
    actor_id = call_user(user_id=user_id, user=user)
    result = await run_in_threadpool(  # allow-run-in-threadpool: TodoStageServiceProtocol method is synchronous.
        service.resume, todo_id=todo_id, actor_id=actor_id
    )
    return envelope(TodoResponse.from_record(result), request)


@router.post("/{todo_id}/cancel", response_model=Envelope[TodoResponse])
@envelope_errors
async def cancel_todo(
    todo_id: Annotated[str, Path(description="A-owned Todo identifier.")],
    body: CancelTodoRequest,
    request: Request,
    user: AuthenticatedUserDep,
    user_id: str = Query(min_length=1, description=USER_ID_DESCRIPTION),
    service: TodoStageServiceProtocol = Injected(TodoStageServiceProtocol),
) -> Envelope[TodoResponse]:
    """Cancel a Todo explicitly as its creator or a Space admin."""
    actor_id = call_user(user_id=user_id, user=user)
    result = await run_in_threadpool(  # allow-run-in-threadpool: TodoStageServiceProtocol method is synchronous.
        service.cancel, todo_id=todo_id, actor_id=actor_id, reason=body.reason
    )
    return envelope(TodoResponse.from_record(result), request)


@router.get("/{todo_id}/events", response_model=Envelope[list[TodoEventResponse]])
@envelope_errors
async def list_todo_events(
    todo_id: Annotated[str, Path(description="A-owned Todo identifier.")],
    request: Request,
    user: AuthenticatedUserDep,
    user_id: str = Query(min_length=1, description=USER_ID_DESCRIPTION),
    service: TodoServiceProtocol = Injected(TodoServiceProtocol),
) -> Envelope[list[TodoEventResponse]]:
    """Project B events for one Todo, or return empty before dispatch."""
    actor_id = call_user(user_id=user_id, user=user)
    events = await run_in_threadpool(
        service.list_events,
        todo_id=todo_id,
        actor_id=actor_id,
    )
    return envelope([TodoEventResponse.from_record(item) for item in events], request)


@router.get(
    "/{todo_id}/interactions",
    response_model=Envelope[list[TodoInteractionResponse]],
)
@envelope_errors
async def list_todo_interactions(
    todo_id: Annotated[str, Path(description="A-owned Todo identifier.")],
    request: Request,
    user: AuthenticatedUserDep,
    user_id: str = Query(min_length=1, description=USER_ID_DESCRIPTION),
    service: TodoServiceProtocol = Injected(TodoServiceProtocol),
) -> Envelope[list[TodoInteractionResponse]]:
    """Return the Human and Bot conversation projection for one Todo."""
    actor_id = call_user(user_id=user_id, user=user)
    events = await run_in_threadpool(
        service.list_interactions,
        todo_id=todo_id,
        actor_id=actor_id,
    )
    return envelope(
        [TodoInteractionResponse.from_record(item) for item in events], request
    )


@router.post(
    "/{todo_id}/interactions", response_model=Envelope[TodoInteractionResponse]
)
@envelope_errors
async def create_todo_interaction(
    todo_id: Annotated[str, Path(description="A-owned Todo identifier.")],
    body: CreateTodoInteractionRequest,
    request: Request,
    response: Response,
    user: AuthenticatedUserDep,
    user_id: str = Query(min_length=1, description=USER_ID_DESCRIPTION),
    service: TodoServiceProtocol = Injected(TodoServiceProtocol),
) -> Envelope[TodoInteractionResponse]:
    """Append one Human interaction event while the Todo awaits acceptance."""
    actor_id = call_user(user_id=user_id, user=user)
    event = await service.create_interaction(
        todo_id=todo_id,
        actor_id=actor_id,
        author_name=None,
        message=body.message,
        reply_to_event_id=body.reply_to_event_id,
        mentions=tuple(
            TodoInteractionMention(
                actor_type=TodoInteractionMentionActorType(mention.actor_type),
                actor_id=mention.actor_id,
            )
            for mention in body.mentions
        ),
    )
    response.status_code = 201
    return created(TodoInteractionResponse.from_record(event), request)


@router.post("/{todo_id}/acceptance", response_model=Envelope[TodoResponse])
@envelope_errors
async def submit_todo_acceptance(
    todo_id: Annotated[str, Path(description="A-owned Todo identifier.")],
    body: SubmitTodoAcceptanceRequest,
    request: Request,
    user: AuthenticatedUserDep,
    user_id: str = Query(min_length=1, description=USER_ID_DESCRIPTION),
    service: TodoServiceProtocol = Injected(TodoServiceProtocol),
) -> Envelope[TodoResponse]:
    """Approve or reject the execution currently pending acceptance."""
    actor_id = call_user(user_id=user_id, user=user)
    result = await run_in_threadpool(
        service.submit_acceptance,
        todo_id=todo_id,
        actor_id=actor_id,
        verdict=body.verdict,
        decision_id=body.decision_id,
        message=body.message,
        targets=tuple(
            TodoAcceptanceTarget(
                kind=TodoAcceptanceTargetKind(target.kind),
                target_id=target.id,
                floor=target.floor,
            )
            for target in body.targets
        ),
    )
    return envelope(TodoResponse.from_record(result), request)


@router.get(
    "/{todo_id}/artifacts",
    response_model=Envelope[list[TodoArtifactRecordResponse]],
)
@envelope_errors
async def list_todo_artifacts(
    todo_id: Annotated[str, Path(description="A-owned Todo identifier.")],
    request: Request,
    user: AuthenticatedUserDep,
    user_id: str = Query(min_length=1, description=USER_ID_DESCRIPTION),
    service: TodoArtifactServiceProtocol = Injected(TodoArtifactServiceProtocol),
) -> Envelope[list[TodoArtifactRecordResponse]]:
    """List independently persisted execution artifacts for one visible Todo."""
    actor_id = call_user(user_id=user_id, user=user)
    records = await run_in_threadpool(
        service.list_artifacts,
        todo_id=todo_id,
        actor_id=actor_id,
    )
    return envelope(
        [TodoArtifactRecordResponse.from_record(record) for record in records],
        request,
    )


@router.get("/{todo_id}/artifacts/{artifact_id}/content")
@envelope_errors
async def read_todo_artifact_content(
    todo_id: Annotated[str, Path(description="A-owned Todo identifier.")],
    artifact_id: Annotated[str, Path(description="Todo artifact identifier.")],
    user: AuthenticatedUserDep,
    user_id: str = Query(min_length=1, description=USER_ID_DESCRIPTION),
    disposition: Literal["inline", "attachment"] = Query(
        default="attachment",
        description="Browser content disposition for preview or download.",
    ),
    service: TodoArtifactServiceProtocol = Injected(TodoArtifactServiceProtocol),
) -> Response:
    """Proxy one file artifact for browser preview or download."""
    actor_id = call_user(user_id=user_id, user=user)
    download = await run_in_threadpool(
        _load_todo_artifact_file,
        service,
        todo_id=todo_id,
        artifact_id=artifact_id,
        actor_id=actor_id,
    )
    content = download.record.content
    file_name = str(content.get("file_name") or "artifact.bin")
    file_type = str(content.get("file_type") or "application/octet-stream")
    return Response(
        content=download.payload,
        media_type=file_type,
        headers={
            "Content-Disposition": (
                f"{disposition}; filename*=UTF-8''{quote(file_name)}"
            ),
            "Content-Length": str(len(download.payload)),
        },
    )
