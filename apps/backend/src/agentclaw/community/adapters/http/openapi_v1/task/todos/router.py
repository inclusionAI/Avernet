"""Human-only Todo workbench routes owned by the A-side product surface."""

from __future__ import annotations

from typing import Annotated, Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, Path, Query, Request, Response
from fastapi.concurrency import run_in_threadpool

from agentclaw.community.adapters.http.openapi_v1.authorization import PublicAPIRoute
from agentclaw.community.adapters.http.openapi_v1.contracts import (
    Envelope,
    Page,
    PageParamsDep,
)
from agentclaw.community.adapters.http.openapi_v1.dependencies import (
    Principal,
    require_principal,
)
from agentclaw.community.adapters.http.openapi_v1.errors import UserIdMismatchError
from agentclaw.community.adapters.http.openapi_v1.principal import (
    USER_ID_DESCRIPTION,
    caller_owner_id,
    refuse_app_only_caller,
)
from agentclaw.community.adapters.http.openapi_v1.responses import (
    created,
    envelope,
    envelope_errors,
    page as page_envelope,
)
from agentclaw.community.api.task.todo_artifact_service import (
    TodoArtifactServiceProtocol,
)
from agentclaw.community.api.task.todo_service import TodoServiceProtocol
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
)
from agentclaw.community.di import Injected

from .schemas import (
    CreateTodoInteractionRequest,
    CreateTodoRequest,
    SubmitTodoAcceptanceRequest,
    CreateTodoFromTemplateRequest,
    TodoArtifactRecordResponse,
    TodoStageResponse,
    TodoEventResponse,
    TodoInteractionResponse,
    TodoResponse,
    TodoStatusSchema,
    CompleteTodoStageRequest,
    CancelTodoRequest,
)
from agentclaw.community.core.task.todos.stage_models import TodoParticipantBindingInput

OwnersDep = Annotated[str, Query(min_length=1, description=USER_ID_DESCRIPTION)]


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


def call_user(*, user_id: str, principal: Principal) -> str:
    """Confirm the named owner is the verified Human and return it."""
    owner_id = caller_owner_id(principal)
    if user_id != owner_id:
        raise UserIdMismatchError(
            f"named user_id does not match authenticated owner {owner_id}"
        )
    return owner_id


router = APIRouter(
    prefix="/openapi/v1/collaboration/tasks/todos",
    tags=["todos"],
    route_class=PublicAPIRoute,
    dependencies=[Depends(refuse_app_only_caller)],
)

PrincipalDep = Annotated[Principal, Depends(require_principal)]


@router.get("", response_model=Envelope[Page[TodoResponse]])
@envelope_errors
async def list_todos(
    request: Request,
    page_params: PageParamsDep,
    principal: PrincipalDep,
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
    actor_id = call_user(user_id=user_id, principal=principal)
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
    principal: PrincipalDep,
    user_id: str = Query(min_length=1, description=USER_ID_DESCRIPTION),
    service: TodoServiceProtocol = Injected(TodoServiceProtocol),
) -> Envelope[TodoResponse]:
    """Create one pending Todo for a Space-eligible Bot and await dispatch."""
    actor_id = call_user(user_id=user_id, principal=principal)
    result = await run_in_threadpool(  # allow-run-in-threadpool
        service.create_todo,
        space_id=body.space_id,
        actor_id=actor_id,
        title=body.title,
        description=body.description,
        bot_id=body.bot_id,
    )
    response.status_code = 201
    return created(TodoResponse.from_record(result), request)


@router.post("/from-template", response_model=Envelope[TodoResponse])
@envelope_errors
async def create_todo_from_template(
    body: CreateTodoFromTemplateRequest,
    request: Request,
    response: Response,
    principal: PrincipalDep,
    user_id: str = Query(min_length=1, description=USER_ID_DESCRIPTION),
    space_id: int = Query(gt=0, description="Product Space that owns the Todo."),
    service: TodoTemplateCreationServiceProtocol = Injected(
        TodoTemplateCreationServiceProtocol
    ),
) -> Envelope[TodoResponse]:
    """Create and expand one Todo from a published product-flow template."""
    actor_id = call_user(user_id=user_id, principal=principal)
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
    principal: PrincipalDep,
    user_id: str = Query(min_length=1, description=USER_ID_DESCRIPTION),
    service: TodoServiceProtocol = Injected(TodoServiceProtocol),
) -> Envelope[TodoResponse]:
    """Return one Todo visible to the authenticated Human."""
    actor_id = call_user(user_id=user_id, principal=principal)
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
    principal: PrincipalDep,
    user_id: str = Query(min_length=1, description=USER_ID_DESCRIPTION),
    service: TodoServiceProtocol = Injected(TodoServiceProtocol),
) -> Envelope[TodoResponse]:
    """Compensate automatic dispatch by running one still-pending Todo."""
    actor_id = call_user(user_id=user_id, principal=principal)
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
    principal: PrincipalDep,
    user_id: str = Query(min_length=1, description=USER_ID_DESCRIPTION),
    service: TodoServiceProtocol = Injected(TodoServiceProtocol),
) -> Envelope[list[TodoStageResponse]]:
    """Return the immutable expanded stage timeline for one Todo."""
    actor_id = call_user(user_id=user_id, principal=principal)
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
    stage_instance_id: Annotated[str, Path(description="Current Human stage instance.")],
    body: CompleteTodoStageRequest,
    request: Request,
    principal: PrincipalDep,
    user_id: str = Query(min_length=1, description=USER_ID_DESCRIPTION),
    service: TodoStageServiceProtocol = Injected(TodoStageServiceProtocol),
) -> Envelope[TodoResponse]:
    """Complete the current Human stage as its owner or a Space admin."""
    actor_id = call_user(user_id=user_id, principal=principal)
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
    principal: PrincipalDep,
    user_id: str = Query(min_length=1, description=USER_ID_DESCRIPTION),
    service: TodoStageServiceProtocol = Injected(TodoStageServiceProtocol),
) -> Envelope[TodoResponse]:
    """Resume an interrupted or blocked Todo."""
    actor_id = call_user(user_id=user_id, principal=principal)
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
    principal: PrincipalDep,
    user_id: str = Query(min_length=1, description=USER_ID_DESCRIPTION),
    service: TodoStageServiceProtocol = Injected(TodoStageServiceProtocol),
) -> Envelope[TodoResponse]:
    """Cancel a Todo explicitly as its creator or a Space admin."""
    actor_id = call_user(user_id=user_id, principal=principal)
    result = await run_in_threadpool(  # allow-run-in-threadpool: TodoStageServiceProtocol method is synchronous.
        service.cancel, todo_id=todo_id, actor_id=actor_id, reason=body.reason
    )
    return envelope(TodoResponse.from_record(result), request)


@router.get("/{todo_id}/events", response_model=Envelope[list[TodoEventResponse]])
@envelope_errors
async def list_todo_events(
    todo_id: Annotated[str, Path(description="A-owned Todo identifier.")],
    request: Request,
    principal: PrincipalDep,
    user_id: str = Query(min_length=1, description=USER_ID_DESCRIPTION),
    service: TodoServiceProtocol = Injected(TodoServiceProtocol),
) -> Envelope[list[TodoEventResponse]]:
    """Project B events for one Todo, or return empty before dispatch."""
    actor_id = call_user(user_id=user_id, principal=principal)
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
    principal: PrincipalDep,
    user_id: str = Query(min_length=1, description=USER_ID_DESCRIPTION),
    service: TodoServiceProtocol = Injected(TodoServiceProtocol),
) -> Envelope[list[TodoInteractionResponse]]:
    """Return the Human and Bot conversation projection for one Todo."""
    actor_id = call_user(user_id=user_id, principal=principal)
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
    principal: PrincipalDep,
    user_id: str = Query(min_length=1, description=USER_ID_DESCRIPTION),
    service: TodoServiceProtocol = Injected(TodoServiceProtocol),
) -> Envelope[TodoInteractionResponse]:
    """Append one Human interaction event while the Todo awaits acceptance."""
    actor_id = call_user(user_id=user_id, principal=principal)
    event = await service.create_interaction(
        todo_id=todo_id,
        actor_id=actor_id,
        author_name=None,
        message=body.message,
    )
    response.status_code = 201
    return created(TodoInteractionResponse.from_record(event), request)


@router.post("/{todo_id}/acceptance", response_model=Envelope[TodoResponse])
@envelope_errors
async def submit_todo_acceptance(
    todo_id: Annotated[str, Path(description="A-owned Todo identifier.")],
    body: SubmitTodoAcceptanceRequest,
    request: Request,
    principal: PrincipalDep,
    user_id: str = Query(min_length=1, description=USER_ID_DESCRIPTION),
    service: TodoServiceProtocol = Injected(TodoServiceProtocol),
) -> Envelope[TodoResponse]:
    """Approve or reject the execution currently pending acceptance."""
    actor_id = call_user(user_id=user_id, principal=principal)
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
    principal: PrincipalDep,
    user_id: str = Query(min_length=1, description=USER_ID_DESCRIPTION),
    service: TodoArtifactServiceProtocol = Injected(TodoArtifactServiceProtocol),
) -> Envelope[list[TodoArtifactRecordResponse]]:
    """List independently persisted execution artifacts for one visible Todo."""
    actor_id = call_user(user_id=user_id, principal=principal)
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
    principal: PrincipalDep,
    user_id: str = Query(min_length=1, description=USER_ID_DESCRIPTION),
    disposition: Literal["inline", "attachment"] = Query(
        default="attachment",
        description="Browser content disposition for preview or download.",
    ),
    service: TodoArtifactServiceProtocol = Injected(TodoArtifactServiceProtocol),
) -> Response:
    """Proxy one file artifact for browser preview or download."""
    actor_id = call_user(user_id=user_id, principal=principal)
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
