"""Owner-only OpenAPI operations for personal Bot dormant lifecycle."""

from __future__ import annotations

from typing import Annotated

from agentclaw.community.adapters.http.openapi_v1.authorization import PublicAPIRoute
from agentclaw.community.adapters.http.openapi_v1.contracts import BotIdPath, Envelope
from agentclaw.community.adapters.http.openapi_v1.engine_runtime.params import (
    OwnerIdDep,
)
from agentclaw.community.adapters.http.openapi_v1.principal import (
    OwnerNameDep,
    UserIdDep,
)
from agentclaw.community.adapters.http.openapi_v1.responses import (
    accepted,
    envelope,
    envelope_errors,
)
from agentclaw.community.api.bot_dormant_service import (
    BotDormantActivateServiceProtocol,
    BotDormantRecycleOperationProtocol,
)
from agentclaw.community.di import Injected
from fastapi import APIRouter, Path, Request, Response
from starlette.concurrency import run_in_threadpool

from .schemas import BotActivateResult, BotRecycleOperation, BotRecycleResult

router = APIRouter(
    prefix="/openapi/v1/bots/{bot_id}",
    tags=["bots"],
    route_class=PublicAPIRoute,
)


@router.post(
    "/recycle",
    response_model=Envelope[BotRecycleResult],
    responses={202: {"model": Envelope[BotRecycleResult]}},
)
@envelope_errors
async def recycle_dormant_bot(
    bot_id: BotIdPath,
    request: Request,
    response: Response,
    owner_id: OwnerIdDep,
    user_id: UserIdDep,
    owner_name: OwnerNameDep,
    service: BotDormantRecycleOperationProtocol = Injected(
        BotDormantRecycleOperationProtocol
    ),
) -> Envelope[BotRecycleResult]:
    """Durably submit recycling; backup and release never run in this request."""
    result = await run_in_threadpool(
        service.submit,
        bot_id=bot_id,
        owner_id=owner_id,
        owner_name=owner_name,
        request_id=getattr(request.state, "request_id", "") or "",
    )
    payload = BotRecycleResult(**result)
    if payload.operation_id is not None:
        response.status_code = 202
        return accepted(payload, request)
    return envelope(payload, request)


@router.get(
    "/recycle-operations/{operation_id}", response_model=Envelope[BotRecycleOperation]
)
@envelope_errors
async def get_recycle_operation(
    bot_id: BotIdPath,
    operation_id: Annotated[
        str, Path(description="Recycle operation handle returned by POST /recycle.")
    ],
    request: Request,
    owner_id: OwnerIdDep,
    user_id: UserIdDep,
    service: BotDormantRecycleOperationProtocol = Injected(
        BotDormantRecycleOperationProtocol
    ),
) -> Envelope[BotRecycleOperation]:
    """Read the matching current/latest operation; never advance or retry it."""
    result = await run_in_threadpool(
        service.get_operation,
        bot_id=bot_id,
        owner_id=owner_id,
        operation_id=operation_id,
    )
    return envelope(BotRecycleOperation(**result), request)


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
    result = await run_in_threadpool(
        service.activate,
        bot_id=bot_id,
        owner_id=owner_id,
        owner_name=owner_name,
    )
    payload = BotActivateResult(**result.__dict__)
    if result.status == "REACTIVATING":
        response.status_code = 202
        return accepted(payload, request)
    return envelope(payload, request)
