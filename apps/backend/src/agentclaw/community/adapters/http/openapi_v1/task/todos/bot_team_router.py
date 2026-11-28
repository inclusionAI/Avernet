"""Human-only HTTP product facade for Todo Bot teams."""

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
from agentclaw.community.api.task.todo_bot_team_service import (
    TodoBotTeamServiceProtocol,
)
from agentclaw.community.core.task.todos.bot_team_models import BotTeamStatus
from agentclaw.community.di import Injected

from .bot_team_schemas import (
    BotTeamDetailResponse,
    BotTeamRecordResponse,
    CreateBotTeamRequest,
    UpdateBotTeamRequest,
)

BotTeamIdPath = Annotated[
    str,
    Path(
        min_length=1,
        max_length=64,
        description="Stable Bot team identifier.",
    ),
]

router = APIRouter(
    prefix="/openapi/v1/collaboration/tasks/bot-teams",
    tags=["bot-teams"],
    route_class=PublicAPIRoute,
    dependencies=[Depends(refuse_app_only_caller)],
)


@router.post(
    "",
    response_model=Envelope[BotTeamDetailResponse],
    responses=USER_SCOPED_ERROR_RESPONSES,
)
@envelope_errors
async def create_bot_team(
    body: CreateBotTeamRequest,
    request: Request,
    response: Response,
    user_id: UserIdDep,
    service: TodoBotTeamServiceProtocol = Injected(TodoBotTeamServiceProtocol),
) -> Envelope[BotTeamDetailResponse]:
    """Create a Bot team and publish its first revision as a Space Admin."""
    detail = await service.create_bot_team(
        space_id=body.space_id,
        actor_id=user_id,
        definition=body.to_definition(),
    )
    response.status_code = 201
    return created(BotTeamDetailResponse.from_detail(detail), request)


@router.get(
    "",
    response_model=Envelope[Page[BotTeamRecordResponse]],
    responses=USER_SCOPED_ERROR_RESPONSES,
)
@envelope_errors
async def list_bot_teams(
    request: Request,
    page_params: PageParamsDep,
    user_id: UserIdDep,
    space_id: int = Query(
        gt=0,
        description="Product Space whose Bot teams are listed.",
    ),
    status: BotTeamStatus | None = Query(
        default=None,
        description="Optional Bot team lifecycle status filter.",
    ),
    service: TodoBotTeamServiceProtocol = Injected(TodoBotTeamServiceProtocol),
) -> Envelope[Page[BotTeamRecordResponse]]:
    """List Bot teams visible to a Space member."""
    result = await run_in_threadpool(
        service.list_bot_teams,
        space_id=space_id,
        actor_id=user_id,
        status=status,
        page=page_params.page,
        page_size=page_params.page_size,
    )
    return page_envelope(
        result.total,
        [BotTeamRecordResponse.from_record(item) for item in result.items],
        request,
    )


@router.get(
    "/{bot_team_id}",
    response_model=Envelope[BotTeamDetailResponse],
    responses=USER_SCOPED_ERROR_RESPONSES,
)
@envelope_errors
async def get_bot_team(
    bot_team_id: BotTeamIdPath,
    request: Request,
    user_id: UserIdDep,
    service: TodoBotTeamServiceProtocol = Injected(TodoBotTeamServiceProtocol),
) -> Envelope[BotTeamDetailResponse]:
    """Read Bot team metadata and its published revision history."""
    detail = await run_in_threadpool(
        service.get_bot_team,
        bot_team_id=bot_team_id,
        actor_id=user_id,
    )
    return envelope(BotTeamDetailResponse.from_detail(detail), request)


@router.put(
    "/{bot_team_id}",
    response_model=Envelope[BotTeamDetailResponse],
    responses=USER_SCOPED_ERROR_RESPONSES,
)
@envelope_errors
async def update_bot_team(
    bot_team_id: BotTeamIdPath,
    body: UpdateBotTeamRequest,
    request: Request,
    user_id: UserIdDep,
    service: TodoBotTeamServiceProtocol = Injected(TodoBotTeamServiceProtocol),
) -> Envelope[BotTeamDetailResponse]:
    """Publish a new immutable Bot team revision as a Space Admin."""
    detail = await service.update_bot_team(
        bot_team_id=bot_team_id,
        actor_id=user_id,
        definition=body.to_definition(),
    )
    return envelope(BotTeamDetailResponse.from_detail(detail), request)


@router.post(
    "/{bot_team_id}/archive",
    response_model=Envelope[BotTeamRecordResponse],
    responses=USER_SCOPED_ERROR_RESPONSES,
)
@envelope_errors
async def archive_bot_team(
    bot_team_id: BotTeamIdPath,
    request: Request,
    user_id: UserIdDep,
    service: TodoBotTeamServiceProtocol = Injected(TodoBotTeamServiceProtocol),
) -> Envelope[BotTeamRecordResponse]:
    """Prevent a Bot team from being used for new Todo creation."""
    record = await run_in_threadpool(
        service.archive_bot_team,
        bot_team_id=bot_team_id,
        actor_id=user_id,
    )
    return envelope(BotTeamRecordResponse.from_record(record), request)
