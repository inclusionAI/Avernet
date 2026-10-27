"""HTTP entrypoints for Bot-public risk applications."""

from __future__ import annotations

import asyncio
import json
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, Response

from agentclaw.community.adapters.http.auth.dependencies import get_current_user
from agentclaw.community.adapters.http.auth.models import AuthenticatedUser
from agentclaw.community.core.bot_public_risk.access import (
    RiskAccessDenied,
    authorize_risk_bot,
)
from agentclaw.community.adapters.http.bot_public.schemas_risk import (
    LegacyRiskRequest,
    OpenApiRiskRequest,
    RiskRecordResponse,
    RiskRefreshResponse,
)
from agentclaw.community.adapters.http.bot_public.schemas import ApiResponse
from agentclaw.community.adapters.http.openapi_v1.authorization import PublicAPIRoute
from agentclaw.community.adapters.http.openapi_v1.contracts import BotIdPath, Envelope
from agentclaw.community.adapters.http.openapi_v1.principal import UserIdDep
from agentclaw.community.adapters.http.openapi_v1.responses import accepted, envelope
from agentclaw.community.core.bot_collaborator.protocols import (
    CollaboratorServiceProtocol,
)
from agentclaw.community.core.bot_public_risk.query import RiskRecordNotFound
from agentclaw.community.api.bot_public_risk_service import (
    BotPublicRiskServiceProtocol,
    BotPublicRiskQueryProtocol,
)
from agentclaw.community.core.bot_public_risk.repository import RequestConflict
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.di import Injected

legacy_router = APIRouter(prefix="/api/bots", tags=["bot-public-risk"])
openapi_router = APIRouter(
    prefix="/openapi/v1/collaboration/bots",
    tags=["collaboration-bot-risk"],
    route_class=PublicAPIRoute,
)


async def _authorize(bots, collaborators, *, bot_id, actor_id, surface, owner_id=None):
    try:
        return await asyncio.to_thread(
            authorize_risk_bot,
            bots,
            collaborators,
            bot_id=bot_id,
            actor_id=actor_id,
            surface=surface,
            legacy_owner_id=owner_id,
        )
    except RiskAccessDenied as exc:
        raise HTTPException(status_code=404, detail="Not found") from exc


def _require_mutable(bot):
    from agentclaw.community.core.bot_management.owner_handoff.contracts import (
        HandoffError,
    )
    from agentclaw.community.core.bot_management.owner_handoff.guard import (
        assert_bot_mutable,
    )

    try:
        assert_bot_mutable(bot.bot)
    except HandoffError as exc:
        raise HTTPException(status_code=404, detail="Not found") from exc


def _view(record):
    target = json.loads(record.target_json)
    workflow = json.loads(record.workflow_json)
    return RiskRecordResponse.model_validate(
        {
            "risk_check_id": record.risk_check_id,
            "phase": record.phase,
            "security_status": record.security_status,
            "detail_url": record.detail_url,
            "auto_poll_paused": record.auto_poll_paused,
            "auto_poll_until": record.auto_poll_until,
            "next_action_at": record.next_action_at,
            "target": target,
            "error_reason": workflow.get("error"),
            "invalidation_reason": workflow.get("invalidation_reason"),
            "approval_puid": record.approval_puid,
        }
    )


async def _record(query, bot, risk_check_id):
    try:
        return await asyncio.to_thread(
            query.get,
            env=bot.bot["env"],
            bot_pk=bot.bot_pk,
            risk_check_id=risk_check_id,
        )
    except RiskRecordNotFound as exc:
        raise HTTPException(status_code=404, detail="Not found") from exc


@openapi_router.post(
    "/{bot_id}/public/risk-checks",
    response_model=Envelope[RiskRecordResponse],
    status_code=202,
)
async def create_openapi(
    bot_id: BotIdPath,
    actor_id: UserIdDep,
    req: OpenApiRiskRequest,
    request: Request,
    response: Response,
    bots: BotRepository = Injected(BotRepository),
    collaborators: CollaboratorServiceProtocol = Injected(CollaboratorServiceProtocol),
    service: BotPublicRiskServiceProtocol = Injected(BotPublicRiskServiceProtocol),
):
    bot = await _authorize(
        bots, collaborators, bot_id=bot_id, actor_id=actor_id, surface="openapi"
    )
    try:
        record, created = await asyncio.to_thread(
            service.submit,
            bot=bot,
            actor_id=actor_id,
            surface="openapi",
            body=req.model_dump(),
        )
    except RequestConflict as exc:
        raise HTTPException(
            status_code=409, detail="Request identifier conflict"
        ) from exc
    if not created:
        response.status_code = 200
        return envelope(_view(record), request)
    return accepted(_view(record), request)


@openapi_router.get(
    "/{bot_id}/public/risk-checks/current", response_model=Envelope[RiskRecordResponse]
)
async def current_openapi(
    bot_id: BotIdPath,
    actor_id: UserIdDep,
    request: Request,
    public_scope: Literal["user", "agent"] = Query(
        ..., description="Publication dimension to read."
    ),
    bots: BotRepository = Injected(BotRepository),
    collaborators: CollaboratorServiceProtocol = Injected(CollaboratorServiceProtocol),
    query: BotPublicRiskQueryProtocol = Injected(BotPublicRiskQueryProtocol),
):
    bot = await _authorize(
        bots, collaborators, bot_id=bot_id, actor_id=actor_id, surface="openapi"
    )
    row = await asyncio.to_thread(
        query.current, env=bot.bot["env"], bot_pk=bot.bot_pk, public_scope=public_scope
    )
    return envelope(_view(row) if row else None, request)


@openapi_router.get(
    "/{bot_id}/public/risk-checks/{risk_check_id}",
    response_model=Envelope[RiskRecordResponse],
)
async def get_openapi(
    bot_id: BotIdPath,
    risk_check_id: Annotated[str, Path(description="Risk application identifier.")],
    actor_id: UserIdDep,
    request: Request,
    bots: BotRepository = Injected(BotRepository),
    collaborators: CollaboratorServiceProtocol = Injected(CollaboratorServiceProtocol),
    query: BotPublicRiskQueryProtocol = Injected(BotPublicRiskQueryProtocol),
):
    bot = await _authorize(
        bots, collaborators, bot_id=bot_id, actor_id=actor_id, surface="openapi"
    )
    return envelope(_view(await _record(query, bot, risk_check_id)), request)


@openapi_router.post(
    "/{bot_id}/public/risk-checks/{risk_check_id}/refresh",
    response_model=Envelope[RiskRefreshResponse],
)
async def refresh_openapi(
    bot_id: BotIdPath,
    risk_check_id: Annotated[str, Path(description="Risk application identifier.")],
    actor_id: UserIdDep,
    request: Request,
    bots: BotRepository = Injected(BotRepository),
    collaborators: CollaboratorServiceProtocol = Injected(CollaboratorServiceProtocol),
    query: BotPublicRiskQueryProtocol = Injected(BotPublicRiskQueryProtocol),
    service: BotPublicRiskServiceProtocol = Injected(BotPublicRiskServiceProtocol),
):
    bot = await _authorize(
        bots, collaborators, bot_id=bot_id, actor_id=actor_id, surface="openapi"
    )
    record = await _record(query, bot, risk_check_id)
    try:
        outcome, record = await asyncio.to_thread(
            service.refresh, bot=bot, actor_id=actor_id, record=record
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Not found") from exc
    return envelope(RiskRefreshResponse(outcome=outcome, record=_view(record)), request)


def _legacy_failure(exc: HTTPException, response: Response) -> ApiResponse:
    response.status_code = exc.status_code
    return ApiResponse(
        success=False, message=str(exc.detail), error_code=exc.status_code, data=None
    )


@legacy_router.post("/{bot_id}/public/risk-checks", response_model=ApiResponse)
async def create_legacy(
    bot_id: str,
    req: LegacyRiskRequest,
    request: Request,
    response: Response,
    user: AuthenticatedUser = Depends(get_current_user),
    bots: BotRepository = Injected(BotRepository),
    collaborators: CollaboratorServiceProtocol = Injected(CollaboratorServiceProtocol),
    service: BotPublicRiskServiceProtocol = Injected(BotPublicRiskServiceProtocol),
):
    try:
        bot = await _authorize(
            bots,
            collaborators,
            bot_id=bot_id,
            actor_id=user.staffId,
            surface="legacy",
            owner_id=req.owner_id,
        )
        _require_mutable(bot)
        record, _ = await asyncio.to_thread(
            service.submit,
            bot=bot,
            actor_id=user.staffId,
            surface="legacy",
            body=req.model_dump(),
        )
        return ApiResponse(
            success=True, message="操作成功", data=_view(record).model_dump(mode="json")
        )
    except RequestConflict:
        return _legacy_failure(
            HTTPException(status_code=409, detail="Request identifier conflict"),
            response,
        )
    except HTTPException as exc:
        return _legacy_failure(exc, response)


@legacy_router.get("/{bot_id}/public/risk-checks/current", response_model=ApiResponse)
async def current_legacy(
    bot_id: str,
    response: Response,
    owner_id: str | None = Query(None, min_length=1),
    user: AuthenticatedUser = Depends(get_current_user),
    bots: BotRepository = Injected(BotRepository),
    collaborators: CollaboratorServiceProtocol = Injected(CollaboratorServiceProtocol),
    query: BotPublicRiskQueryProtocol = Injected(BotPublicRiskQueryProtocol),
):
    try:
        bot = await _authorize(
            bots,
            collaborators,
            bot_id=bot_id,
            actor_id=user.staffId,
            surface="legacy",
            owner_id=owner_id,
        )
        row = await asyncio.to_thread(
            query.current, env=bot.bot["env"], bot_pk=bot.bot_pk, public_scope="legacy"
        )
        return ApiResponse(
            success=True,
            message="查询成功",
            data=_view(row).model_dump(mode="json") if row else None,
        )
    except HTTPException as exc:
        return _legacy_failure(exc, response)


@legacy_router.get(
    "/{bot_id}/public/risk-checks/{risk_check_id}", response_model=ApiResponse
)
async def get_legacy(
    bot_id: str,
    risk_check_id: str,
    response: Response,
    owner_id: str | None = Query(None, min_length=1),
    user: AuthenticatedUser = Depends(get_current_user),
    bots: BotRepository = Injected(BotRepository),
    collaborators: CollaboratorServiceProtocol = Injected(CollaboratorServiceProtocol),
    query: BotPublicRiskQueryProtocol = Injected(BotPublicRiskQueryProtocol),
):
    try:
        bot = await _authorize(
            bots,
            collaborators,
            bot_id=bot_id,
            actor_id=user.staffId,
            surface="legacy",
            owner_id=owner_id,
        )
        row = await _record(query, bot, risk_check_id)
        return ApiResponse(
            success=True, message="查询成功", data=_view(row).model_dump(mode="json")
        )
    except HTTPException as exc:
        return _legacy_failure(exc, response)


@legacy_router.post(
    "/{bot_id}/public/risk-checks/{risk_check_id}/refresh", response_model=ApiResponse
)
async def refresh_legacy(
    bot_id: str,
    risk_check_id: str,
    request: Request,
    response: Response,
    owner_id: str | None = Query(None, min_length=1),
    user: AuthenticatedUser = Depends(get_current_user),
    bots: BotRepository = Injected(BotRepository),
    collaborators: CollaboratorServiceProtocol = Injected(CollaboratorServiceProtocol),
    query: BotPublicRiskQueryProtocol = Injected(BotPublicRiskQueryProtocol),
    service: BotPublicRiskServiceProtocol = Injected(BotPublicRiskServiceProtocol),
):
    try:
        bot = await _authorize(
            bots,
            collaborators,
            bot_id=bot_id,
            actor_id=user.staffId,
            surface="legacy",
            owner_id=owner_id,
        )
        _require_mutable(bot)
        record = await _record(query, bot, risk_check_id)
        try:
            outcome, record = await asyncio.to_thread(
                service.refresh, bot=bot, actor_id=user.staffId, record=record
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail="Not found") from exc
        data = RiskRefreshResponse(outcome=outcome, record=_view(record)).model_dump(
            mode="json"
        )
        return ApiResponse(success=True, message="查询成功", data=data)
    except HTTPException as exc:
        return _legacy_failure(exc, response)
