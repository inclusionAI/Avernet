"""Thin public HTTP adapter for the product Bot Chat query service."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query, Request

from agentclaw.community.adapters.http.openapi_v1.contracts import (
    BotIdPath,
    Envelope,
)
from agentclaw.community.adapters.http.openapi_v1.principal import (
    UserIdDep,
    require_granted_addressed_bot,
)
from agentclaw.community.adapters.http.openapi_v1.responses import (
    envelope,
    envelope_errors,
)
from agentclaw.community.api.bot_chat_service import BotChatServiceProtocol
from agentclaw.community.core.bot_chat.errors import (
    InvalidBotLogQueryError,
    SessionNotFoundError,
)
from agentclaw.community.core.bot_chat.schemas import (
    ConversationDetail,
    SessionListResponse,
)
from agentclaw.community.di import Injected

router = APIRouter(
    prefix="/openapi/v1/bots/{bot_id}/chats",
    tags=["bot-chats"],
)

_GRANT_CHECKED_ADDRESSED_BOT = [Depends(require_granted_addressed_bot)]


@router.get(
    "",
    response_model=Envelope[SessionListResponse],
    dependencies=_GRANT_CHECKED_ADDRESSED_BOT,
)
@envelope_errors
async def list_bot_chats(
    request: Request,
    bot_id: BotIdPath,
    user_id: UserIdDep,
    owner_id: str | None = Query(
        default=None,
        min_length=1,
        description="Owner of the addressed bot; defaults to user_id. Name it "
        "only when the bot is shared with the acting user.",
    ),
    trace_id: str | None = Query(default=None, description="Filter by trace id."),
    session_id: str | None = Query(
        default=None, description="Filter by the engine session id."
    ),
    session_key: str | None = Query(
        default=None, description="Filter by the engine session key."
    ),
    query: str | None = Query(
        default=None,
        description="Fuzzy search over the chat name and user input.",
    ),
    biz_scene: str | None = Query(default=None, description="Business scene."),
    biz_task_id: str | None = Query(default=None, description="Business task id."),
    group_id: str | None = Query(default=None, description="BCS group id."),
    match_mode: str = Query(default="exact", pattern="^(exact|contains)$"),
    include_output_match: bool = Query(
        default=False,
        description="Include trace output when applying the keyword query.",
    ),
    time_scope: str = Query(default="default", pattern="^(default|all)$"),
    from_date: datetime | None = Query(default=None, description="ISO 8601 start time."),
    to_date: datetime | None = Query(default=None, description="ISO 8601 end time."),
    page: int = Query(default=1, ge=1, description="1-based page number."),
    limit: int = Query(default=20, ge=1, le=100, description="Chats per page."),
    log_source: str | None = Query(
        default=None,
        description="Data source override: 'db' or 'langfuse'.",
    ),
    service: BotChatServiceProtocol = Injected(BotChatServiceProtocol),
) -> Envelope[SessionListResponse]:
    """List one addressed Bot's product chat records for the acting user."""
    # ``owner_id`` is consumed by the addressed-bot grant dependency. Product
    # chat visibility remains scoped to the acting user and is re-adjudicated
    # by BotChatService (owner or collaborator), exactly like the product API.
    del owner_id
    try:
        result = await service.list_sessions(
            owner_id=user_id,
            from_date=from_date,
            to_date=to_date,
            page=page,
            limit=limit,
            bot_id=bot_id,
            trace_id=trace_id,
            session_id=session_id,
            session_key=session_key,
            query=query,
            biz_scene=biz_scene,
            biz_task_id=biz_task_id,
            group_id=group_id,
            match_mode=match_mode,
            include_output_match=include_output_match,
            time_scope=time_scope,
            log_source=log_source,
        )
    except ValueError as exc:
        raise InvalidBotLogQueryError("invalid product chat query") from exc
    return envelope(result, request)


@router.get(
    "/{trace_id}",
    response_model=Envelope[ConversationDetail],
    dependencies=_GRANT_CHECKED_ADDRESSED_BOT,
)
@envelope_errors
async def get_bot_chat(
    request: Request,
    bot_id: BotIdPath,
    trace_id: str,
    user_id: UserIdDep,
    owner_id: str | None = Query(
        default=None,
        min_length=1,
        description="Owner of the addressed bot; defaults to user_id. Name it "
        "only when the bot is shared with the acting user.",
    ),
    log_source: str | None = Query(
        default=None,
        description="Data source override: 'db' or 'langfuse'.",
    ),
    service: BotChatServiceProtocol = Injected(BotChatServiceProtocol),
) -> Envelope[ConversationDetail]:
    """Return one product chat Trace and its observation tree."""
    del owner_id
    result = await service.get_session(
        trace_id=trace_id,
        owner_id=user_id,
        log_source=log_source,
    )
    # The grant is checked against the bot in the path. The fetched Trace must
    # belong to that same bot, or a grant on one bot could authorize a Trace id
    # from another bot the delegating user can reach.
    if result.bot_id != bot_id:
        raise SessionNotFoundError("trace does not belong to the addressed bot")
    return envelope(result, request)
