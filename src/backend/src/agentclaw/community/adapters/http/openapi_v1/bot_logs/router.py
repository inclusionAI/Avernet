"""Thin HTTP adapter for Bot Trace and observation queries."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Request

from agentclaw.community.adapters.http.openapi_v1.contracts import Envelope
from agentclaw.community.adapters.http.openapi_v1.dependencies import (
    Principal,
    require_user_and_app_principal,
)
from agentclaw.community.adapters.http.openapi_v1.responses import (
    envelope,
    envelope_errors,
)
from agentclaw.community.api.bot_chat_service import OpenBotChatServiceProtocol
from agentclaw.community.core.bot_chat.schemas import (
    ConversationDetail,
    SessionListResponse,
)
from agentclaw.community.di import Injected

router = APIRouter(prefix="/openapi/v1/bots/logs", tags=["bot-logs"])

PrincipalDep = Annotated[Principal, Depends(require_user_and_app_principal)]


@router.get(
    "/sessions/{session_key}/traces",
    response_model=Envelope[SessionListResponse],
)
@envelope_errors
async def list_session_traces(
    request: Request,
    principal: PrincipalDep,
    session_key: str = Path(min_length=1, max_length=512),
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=100, ge=1, le=100),
    service: OpenBotChatServiceProtocol = Injected(OpenBotChatServiceProtocol),
) -> Envelope[SessionListResponse]:
    """List traces for one exact Session key."""
    del principal
    result = await service.list_open_sessions(
        session_key=session_key,
        page=page,
        limit=limit,
    )
    return envelope(result, request)


@router.get(
    "/tasks/{biz_scene}/{biz_task_id}/traces",
    response_model=Envelope[SessionListResponse],
)
@envelope_errors
async def list_task_traces(
    request: Request,
    principal: PrincipalDep,
    biz_scene: str = Path(min_length=1, max_length=128),
    biz_task_id: str = Path(min_length=1, max_length=256),
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=100, ge=1, le=100),
    service: OpenBotChatServiceProtocol = Injected(OpenBotChatServiceProtocol),
) -> Envelope[SessionListResponse]:
    """List traces for one exact business Task."""
    del principal
    result = await service.list_open_sessions(
        biz_scene=biz_scene,
        biz_task_id=biz_task_id,
        page=page,
        limit=limit,
    )
    return envelope(result, request)


@router.get(
    "/groups/{group_id}/traces",
    response_model=Envelope[SessionListResponse],
)
@envelope_errors
async def list_group_traces(
    request: Request,
    principal: PrincipalDep,
    group_id: str = Path(min_length=1, max_length=256),
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=100, ge=1, le=100),
    service: OpenBotChatServiceProtocol = Injected(OpenBotChatServiceProtocol),
) -> Envelope[SessionListResponse]:
    """List traces for one exact BCS Group."""
    del principal
    result = await service.list_open_sessions(
        group_id=group_id,
        page=page,
        limit=limit,
    )
    return envelope(result, request)


@router.get("/traces", response_model=Envelope[SessionListResponse])
@envelope_errors
async def list_user_bot_traces(
    request: Request,
    principal: PrincipalDep,
    user_id: str = Query(min_length=1, max_length=256),
    bot_id: str = Query(min_length=1, max_length=256),
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=100, ge=1, le=100),
    service: OpenBotChatServiceProtocol = Injected(OpenBotChatServiceProtocol),
) -> Envelope[SessionListResponse]:
    """List recent traces for one explicit user-and-Bot pair."""
    del principal
    result = await service.list_open_user_bot_traces(
        user_id=user_id,
        bot_id=bot_id,
        page=page,
        limit=limit,
    )
    return envelope(result, request)


@router.get("/traces/{trace_id}", response_model=Envelope[ConversationDetail])
@envelope_errors
async def get_trace(
    request: Request,
    principal: PrincipalDep,
    trace_id: str = Path(min_length=1, max_length=256),
    service: OpenBotChatServiceProtocol = Injected(OpenBotChatServiceProtocol),
) -> Envelope[ConversationDetail]:
    """Return one Trace with its observation tree."""
    del principal
    result = await service.get_open_session(trace_id)
    return envelope(result, request)
