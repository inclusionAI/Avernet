"""Human-only Todo workbench routes owned by the A-side product surface."""

from __future__ import annotations

from typing import Annotated

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
from agentclaw.community.api.task.todo_service import TodoServiceProtocol
from agentclaw.community.core.task.todos.models import (
    MAX_BOT_ID_LENGTH,
    MAX_TITLE_LENGTH,
)
from agentclaw.community.di import Injected

from .schemas import (
    CreateTodoInteractionRequest,
    CreateTodoRequest,
    SubmitTodoAcceptanceRequest,
    TodoEventResponse,
    TodoResponse,
    TodoStatusSchema,
)

OwnersDep = Annotated[str, Query(min_length=1, description=USER_ID_DESCRIPTION)]


def call_user(*, user_id: str, principal: Principal) -> str:
    """Confirm the named owner is the verified Human and return it."""
    owner_id = caller_owner_id(principal)
    if user_id != owner_id:
        raise UserIdMismatchError(
            f"named user_id does not match authenticated owner {owner_id}"
        )
    return owner_id


router = APIRouter(
    prefix="/openapi/v1/todos",
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
    """Create one Todo and its B collaboration Context for a Space-eligible Bot."""
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
    """Start the B collaboration Context already bound to a draft Todo."""
    actor_id = call_user(user_id=user_id, principal=principal)
    result = await service.start_todo(
        todo_id=todo_id,
        actor_id=actor_id,
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
    """Project the complete B event stream for one running Todo."""
    actor_id = call_user(user_id=user_id, principal=principal)
    events = await run_in_threadpool(
        service.list_events,
        todo_id=todo_id,
        actor_id=actor_id,
    )
    return envelope([TodoEventResponse.from_record(item) for item in events], request)


@router.get("/{todo_id}/interactions", response_model=Envelope[list[TodoEventResponse]])
@envelope_errors
async def list_todo_interactions(
    todo_id: Annotated[str, Path(description="A-owned Todo identifier.")],
    request: Request,
    principal: PrincipalDep,
    user_id: str = Query(min_length=1, description=USER_ID_DESCRIPTION),
    service: TodoServiceProtocol = Injected(TodoServiceProtocol),
) -> Envelope[list[TodoEventResponse]]:
    """Return Human interaction events projected from B."""
    actor_id = call_user(user_id=user_id, principal=principal)
    events = await run_in_threadpool(
        service.list_interactions,
        todo_id=todo_id,
        actor_id=actor_id,
    )
    return envelope([TodoEventResponse.from_record(item) for item in events], request)


@router.post("/{todo_id}/interactions", response_model=Envelope[TodoEventResponse])
@envelope_errors
async def create_todo_interaction(
    todo_id: Annotated[str, Path(description="A-owned Todo identifier.")],
    body: CreateTodoInteractionRequest,
    request: Request,
    response: Response,
    principal: PrincipalDep,
    user_id: str = Query(min_length=1, description=USER_ID_DESCRIPTION),
    service: TodoServiceProtocol = Injected(TodoServiceProtocol),
) -> Envelope[TodoEventResponse]:
    """Append one Human interaction event while the Todo awaits acceptance."""
    actor_id = call_user(user_id=user_id, principal=principal)
    event = await run_in_threadpool(  # allow-run-in-threadpool
        service.create_interaction,
        todo_id=todo_id,
        actor_id=actor_id,
        author_name=None,
        message=body.message,
    )
    response.status_code = 201
    return created(TodoEventResponse.from_record(event), request)


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
        message=body.message,
    )
    return envelope(TodoResponse.from_record(result), request)
