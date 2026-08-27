"""Public Caller identity context and per-MCP identity configuration."""

from __future__ import annotations

import asyncio
from typing import Annotated

from fastapi import APIRouter, Path, Query, Request

from agentclaw.community.adapters.http.openapi_v1.authorization import PublicAPIRoute
from agentclaw.community.adapters.http.openapi_v1.contracts import BotIdPath, Envelope
from agentclaw.community.adapters.http.openapi_v1.engine_runtime.params import (
    OwnerIdDep,
)
from agentclaw.community.adapters.http.openapi_v1.engine_runtime.enums import (
    RuntimeStage,
)
from agentclaw.community.adapters.http.openapi_v1.errors import (
    CallerIdentityInvalidError,
)
from agentclaw.community.adapters.http.openapi_v1.principal import UserIdDep
from agentclaw.community.adapters.http.openapi_v1.responses import (
    envelope,
    envelope_errors,
)
from agentclaw.community.api.caller_identity_service import (
    CallerIdentityServiceProtocol,
    CallerIdentityStage as CoreCallerIdentityStage,
    McpCallType as CoreMcpCallType,
)
from agentclaw.community.api.collaborator_lock_service import (
    CollaboratorLockServiceProtocol,
)
from agentclaw.community.di import Injected

from .schemas import (
    CallerCallType,
    CallerContext,
    McpCallTypeResult,
    McpCallTypeUpdate,
)


router = APIRouter(
    prefix="/openapi/v1/bots/{bot_id}",
    tags=["Caller identity"],
    route_class=PublicAPIRoute,
)


def _validate_stage(stage: RuntimeStage, publish_id: int | None) -> None:
    if stage is RuntimeStage.DRAFT:
        if publish_id is not None:
            raise CallerIdentityInvalidError("draft stage does not accept publish_id")
        return
    if publish_id is None:
        raise CallerIdentityInvalidError("published stages require publish_id")


async def _current_lock_epoch(
    locks: CollaboratorLockServiceProtocol,
    *,
    bot_id: str,
    owner_id: str,
    user_id: str,
) -> int | None:
    """Carry the lock validated by ``Check(..., EDIT_LOCK)`` into the write."""
    info = await asyncio.to_thread(
        locks.get_lock_info,
        bot_id=bot_id,
        owner_id=owner_id,
        user_id=user_id,
    )
    lock = info.lock
    if lock is None or lock.holder_user_id != user_id:
        return None
    epoch = lock.id
    return epoch if isinstance(epoch, int) and not isinstance(epoch, bool) else None


@router.get("/caller-context", response_model=Envelope[CallerContext])
@envelope_errors
async def get_caller_context(
    bot_id: BotIdPath,
    request: Request,
    user_id: UserIdDep,
    owner_id: OwnerIdDep,
    stage: RuntimeStage = Query(
        default=RuntimeStage.DRAFT,
        description="Bot runtime stage whose Caller identity context to read.",
    ),
    publish_id: Annotated[
        int | None,
        Query(
            gt=0,
            description="Required publication id for verify and online; omitted for draft.",
        ),
    ] = None,
    service: CallerIdentityServiceProtocol = Injected(CallerIdentityServiceProtocol),
) -> Envelope[CallerContext]:
    """Return aggregate and per-MCP Caller identity for one Bot runtime."""
    _validate_stage(stage, publish_id)
    result = await asyncio.to_thread(
        service.get_context,
        bot_id=bot_id,
        actor_id=user_id,
        stage=CoreCallerIdentityStage(stage.value),
        publish_id=publish_id,
        entity_id=owner_id,
    )
    return envelope(
        CallerContext(
            capability=result.capability,
            stage=RuntimeStage(result.stage.value),
            publish_id=result.publish_id,
            bot_call_type=CallerCallType(result.bot_call_type.value),
            mcp_call_types={
                code: CallerCallType(call_type.value)
                for code, call_type in result.mcp_call_types.items()
            },
            editable=result.editable,
        ),
        request,
    )


@router.patch(
    "/mcps/{server_code}/call-type",
    response_model=Envelope[McpCallTypeResult],
)
@envelope_errors
async def update_mcp_call_type(
    bot_id: BotIdPath,
    server_code: Annotated[str, Path(description="Opaque MCP server identifier.")],
    body: McpCallTypeUpdate,
    request: Request,
    user_id: UserIdDep,
    owner_id: OwnerIdDep,
    service: CallerIdentityServiceProtocol = Injected(CallerIdentityServiceProtocol),
    locks: CollaboratorLockServiceProtocol = Injected(CollaboratorLockServiceProtocol),
) -> Envelope[McpCallTypeResult]:
    """Update one active draft MCP's Caller identity mode."""
    lock_epoch = await _current_lock_epoch(
        locks,
        bot_id=bot_id,
        owner_id=owner_id,
        user_id=user_id,
    )
    result = await service.update_mcp_call_type(
        bot_id=bot_id,
        server_code=server_code,
        call_type=CoreMcpCallType(body.call_type.value),
        actor_id=user_id,
        lock_epoch=lock_epoch,
        entity_id=owner_id,
    )
    return envelope(
        McpCallTypeResult(
            server_code=result.server_code,
            call_type=CallerCallType(result.call_type.value),
            bot_call_type=CallerCallType(result.bot_call_type.value),
        ),
        request,
    )


__all__ = ["router"]
