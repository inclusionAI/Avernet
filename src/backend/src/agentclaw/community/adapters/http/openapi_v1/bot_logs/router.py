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
    session_key: str = Path(
        min_length=1,
        max_length=512,
        description="The engine session key, exactly as a trace reports it "
        "in session_key (exact match, no prefix search).",
    ),
    page: int = Query(default=1, ge=1, description="1-based page number."),
    limit: int = Query(
        default=100, ge=1, le=100, description="Traces per page (max 100)."
    ),
    service: OpenBotChatServiceProtocol = Injected(OpenBotChatServiceProtocol),
) -> Envelope[SessionListResponse]:
    """List the recorded chat turns (traces) of one session, newest first.

    The session key is matched exactly, as reported in a trace's
    session_key.
    """
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
    biz_scene: str = Path(
        min_length=1,
        max_length=128,
        description="The integrating system's business scene the task "
        "belongs to (exact match).",
    ),
    biz_task_id: str = Path(
        min_length=1,
        max_length=256,
        description="The integrating system's own task id within the scene "
        "(exact match).",
    ),
    page: int = Query(default=1, ge=1, description="1-based page number."),
    limit: int = Query(
        default=100, ge=1, le=100, description="Traces per page (max 100)."
    ),
    service: OpenBotChatServiceProtocol = Injected(OpenBotChatServiceProtocol),
) -> Envelope[SessionListResponse]:
    """List the traces labelled with one business scene/task pair, newest first.

    Matches labels attached at recording time and labels registered as
    relations afterwards — each item's match_sources says which.
    """
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
    group_id: str = Path(
        min_length=1,
        max_length=256,
        description="The collaboration group whose sessions' traces to list.",
    ),
    page: int = Query(default=1, ge=1, description="1-based page number."),
    limit: int = Query(
        default=100, ge=1, le=100, description="Traces per page (max 100)."
    ),
    service: OpenBotChatServiceProtocol = Injected(OpenBotChatServiceProtocol),
) -> Envelope[SessionListResponse]:
    """List the traces of every session in one collaboration group, newest first."""
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
    user_id: str = Query(
        min_length=1,
        max_length=256,
        description="Whose traces to read — a filter, not the caller's "
        "identity; naming another user is allowed here.",
    ),
    bot_id: str = Query(
        min_length=1,
        max_length=256,
        description="The bot whose traces to read, paired with user_id.",
    ),
    page: int = Query(default=1, ge=1, description="1-based page number."),
    limit: int = Query(
        default=100, ge=1, le=100, description="Traces per page (max 100)."
    ),
    service: OpenBotChatServiceProtocol = Injected(OpenBotChatServiceProtocol),
) -> Envelope[SessionListResponse]:
    """List one user-and-bot pair's recent traces, newest first.

    Covers the last 72 hours only; use the session, task or group listings
    for older history.
    """
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
    trace_id: str = Path(
        min_length=1,
        max_length=256,
        description="The trace's id, as returned in a listing entry's id.",
    ),
    service: OpenBotChatServiceProtocol = Injected(OpenBotChatServiceProtocol),
) -> Envelope[ConversationDetail]:
    """Return one recorded chat turn (trace) in full.

    Includes the complete input and output plus the observation tree — the
    model generations and tool calls that made up the turn.
    """
    del principal
    result = await service.get_open_session(trace_id)
    return envelope(result, request)
