"""Owner-only OpenAPI operations for personal Bot dormant lifecycle."""

from __future__ import annotations

from fastapi import APIRouter, Request, Response

from agentclaw.community.adapters.http.openapi_v1.authorization import PublicAPIRoute
from agentclaw.community.adapters.http.openapi_v1.contracts import BotIdPath, Envelope
from agentclaw.community.adapters.http.openapi_v1.engine_runtime.params import OwnerIdDep
from agentclaw.community.adapters.http.openapi_v1.principal import OwnerNameDep, UserIdDep
from agentclaw.community.adapters.http.openapi_v1.responses import (
    accepted,
    envelope,
    envelope_errors,
)
from agentclaw.community.api.bot_dormant_service import (
    BotDormantActivateServiceProtocol,
    BotDormantAuditServiceProtocol,
    BotDormantRecycleServiceProtocol,
)
from agentclaw.community.di import Injected

from .schemas import BotActivateResult, BotRecycleResult


router = APIRouter(
    prefix="/openapi/v1/bots/{bot_id}",
    tags=["bots"],
    route_class=PublicAPIRoute,
)


@router.post(
    "/recycle",
    response_model=Envelope[BotRecycleResult],
)
@envelope_errors
async def recycle_dormant_bot(
    bot_id: BotIdPath,
    request: Request,
    owner_id: OwnerIdDep,
    user_id: UserIdDep,
    owner_name: OwnerNameDep,
    service: BotDormantRecycleServiceProtocol = Injected(
        BotDormantRecycleServiceProtocol
    ),
    audit_service: BotDormantAuditServiceProtocol = Injected(
        BotDormantAuditServiceProtocol
    ),
) -> Envelope[BotRecycleResult]:
    """Recycle an active personal managed-cloud Bot synchronously."""
    result = service.recycle(
        bot_id=bot_id,
        owner_id=owner_id,
        owner_name=owner_name,
    )
    payload = BotRecycleResult(**result.__dict__)
    wrapped = envelope(payload, request)
    if result.changed:
        audit_service.record_openapi_recycle(
            request_id=wrapped.request_id,
            bot_id=bot_id,
            owner_id=owner_id,
        )
    return wrapped


@router.post(
    "/activate",
    response_model=Envelope[BotActivateResult],
    responses={202: {"model": Envelope[BotActivateResult]}},
)
@envelope_errors
async def activate_dormant_bot(
    bot_id: BotIdPath,
    request: Request,
    response: Response,
    owner_id: OwnerIdDep,
    user_id: UserIdDep,
    owner_name: OwnerNameDep,
    service: BotDormantActivateServiceProtocol = Injected(
        BotDormantActivateServiceProtocol
    ),
) -> Envelope[BotActivateResult]:
    """Start or observe reactivation of a recycled personal managed-cloud Bot."""
    result = service.activate(
        bot_id=bot_id,
        owner_id=owner_id,
        owner_name=owner_name,
    )
    payload = BotActivateResult(**result.__dict__)
    if result.status == "REACTIVATING":
        response.status_code = 202
        return accepted(payload, request)
    return envelope(payload, request)
