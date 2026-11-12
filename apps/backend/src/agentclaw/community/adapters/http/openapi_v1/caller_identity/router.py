"""Public Caller identity context and per-MCP identity configuration."""

from __future__ import annotations

import asyncio
import time
from typing import Annotated

from agentclaw.community.adapters.http.openapi_v1.authorization import PublicAPIRoute
from agentclaw.community.adapters.http.openapi_v1.bot_address import resolve_bot_entity
from agentclaw.community.adapters.http.openapi_v1.contracts import BotIdPath, Envelope
from agentclaw.community.adapters.http.openapi_v1.engine_runtime.enums import (
    RuntimeStage,
)
from agentclaw.community.adapters.http.openapi_v1.engine_runtime.params import (
    OwnerIdDep,
)
from agentclaw.community.adapters.http.openapi_v1.errors import (
    CallerIdentityInvalidError,
)
from agentclaw.community.adapters.http.openapi_v1.principal import UserIdDep
from agentclaw.community.adapters.http.openapi_v1.response_envelope import (
    error_response,
)
from agentclaw.community.adapters.http.openapi_v1.responses import (
    envelope,
    envelope_errors,
)
from agentclaw.community.api.caller_credential import CallerCredentialError
from agentclaw.community.api.caller_identity_service import (
    CallerIdentityServiceProtocol,
)
from agentclaw.community.api.caller_identity_service import (
    CallerIdentityStage as CoreCallerIdentityStage,
)
from agentclaw.community.api.caller_identity_service import (
    McpCallType as CoreMcpCallType,
)
from agentclaw.community.api.collaborator_lock_service import (
    CollaboratorLockServiceProtocol,
)
from agentclaw.community.api.scheduled_caller_refresh import (
    ScheduledCallerRefreshProtocol,
)
from agentclaw.community.core.caller_identity.contracts import (
    CallerIdentityNotFoundError,
    CallerIdentityPermissionError,
    CallerIdentityReadOnlyError,
)
from agentclaw.community.di import Injected
from agentclaw.community.log import get_logger
from fastapi import APIRouter, Path, Query, Request

from .schemas import (
    CallerCallType,
    CallerContext,
    CallerRefreshResult,
    CliCallTypeResult,
    McpCallTypeResult,
    McpCallTypeUpdate,
)

router = APIRouter(
    prefix="/openapi/v1/bots/{bot_id}",
    tags=["Caller identity"],
    route_class=PublicAPIRoute,
)
logger = get_logger()


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
    """Return aggregate and per-resource Caller overrides for one Bot runtime."""
    _validate_stage(stage, publish_id)
    entity_id = await resolve_bot_entity(request, bot_id=bot_id, locator=owner_id)
    result = await asyncio.to_thread(
        service.get_context,
        bot_id=bot_id,
        actor_id=user_id,
        stage=CoreCallerIdentityStage(stage.value),
        publish_id=publish_id,
        entity_id=entity_id,
    )
    logger.info(
        "openapi_caller_context_get_succeeded bot_id=%s stage=%s publish_id=%s "
        "mcp_caller_override_count=%s cli_caller_override_count=%s",
        bot_id,
        stage.value,
        publish_id,
        len(result.mcp_call_types),
        len(result.cli_call_types),
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
            cli_call_types={
                code: CallerCallType(call_type.value)
                for code, call_type in result.cli_call_types.items()
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
    entity_id = await resolve_bot_entity(request, bot_id=bot_id, locator=owner_id)
    result = await service.update_mcp_call_type(
        bot_id=bot_id,
        server_code=server_code,
        call_type=CoreMcpCallType(body.call_type.value),
        actor_id=user_id,
        lock_epoch=lock_epoch,
        entity_id=entity_id,
    )
    return envelope(
        McpCallTypeResult(
            server_code=result.server_code,
            call_type=CallerCallType(result.call_type.value),
            bot_call_type=CallerCallType(result.bot_call_type.value),
        ),
        request,
    )


@router.patch(
    "/clis/{cli_code}/call-type",
    response_model=Envelope[CliCallTypeResult],
)
@envelope_errors
async def update_cli_call_type(
    bot_id: BotIdPath,
    cli_code: Annotated[str, Path(description="Opaque AgentPass CLI identifier.")],
    body: McpCallTypeUpdate,
    request: Request,
    user_id: UserIdDep,
    owner_id: OwnerIdDep,
    service: CallerIdentityServiceProtocol = Injected(CallerIdentityServiceProtocol),
    locks: CollaboratorLockServiceProtocol = Injected(CollaboratorLockServiceProtocol),
) -> Envelope[CliCallTypeResult]:
    """Update one authorized Default CLI's AgentPass identity mode."""
    lock_epoch = await _current_lock_epoch(
        locks,
        bot_id=bot_id,
        owner_id=owner_id,
        user_id=user_id,
    )
    entity_id = await resolve_bot_entity(request, bot_id=bot_id, locator=owner_id)
    result = await service.update_cli_call_type(
        bot_id=bot_id,
        cli_code=cli_code,
        call_type=CoreMcpCallType(body.call_type.value),
        actor_id=user_id,
        lock_epoch=lock_epoch,
        entity_id=entity_id,
    )
    return envelope(
        CliCallTypeResult(
            cli_code=result.cli_code,
            call_type=CallerCallType(result.call_type.value),
            bot_call_type=CallerCallType(result.bot_call_type.value),
        ),
        request,
    )


__all__ = ["router"]


@router.post("/caller-token/refresh", response_model=Envelope[CallerRefreshResult])
@envelope_errors
async def refresh_caller_token(
    bot_id: BotIdPath, request: Request, user_id: UserIdDep, owner_id: OwnerIdDep,
    service: ScheduledCallerRefreshProtocol = Injected(ScheduledCallerRefreshProtocol),
) -> Envelope[CallerRefreshResult]:
    """Refresh eligible Caller runtimes from stored ASC independently of Owner renewal.

    Requires the current Bot owner and an addressed Bot application grant. Runtime
    targets must be alive and have an enabled Cron task. Partial failures are
    returned explicitly; credentials are installed directly and never returned.
    """
    started = time.monotonic()
    fields = {"bot_id": bot_id, "actor_id": user_id, "locator": owner_id,
              "trace_id": getattr(request.state, "trace_id", None)}
    logger.info("openapi_caller_refresh_requested system=backend direction=inbound operation=refresh_caller_token method=POST route=/openapi/v1/bots/{bot_id}/caller-token/refresh fields=%s", fields)
    try:
        entity_id = await resolve_bot_entity(request, bot_id=bot_id, locator=owner_id)
        result = await asyncio.to_thread(service.refresh_for_actor,
            bot_id=bot_id, actor_id=user_id, entity_id=entity_id)
        data = CallerRefreshResult.model_validate(result)
    except Exception as exc:
        logger.warning("openapi_caller_refresh_failed system=backend operation=refresh_caller_token fields=%s error_type=%s duration_ms=%.1f",
                       fields, type(exc).__name__, (time.monotonic() - started) * 1000)
        if isinstance(exc, (CallerIdentityNotFoundError, CallerIdentityPermissionError, CallerIdentityReadOnlyError)):
            raise
        if isinstance(exc, CallerCredentialError):
            if exc.code == "CALLER_CREDENTIAL_PROVIDER_UNAVAILABLE":
                return error_response(501, "Stored ASC Caller refresh is not supported", request)
            return error_response(400, "Caller refresh is not available for this Bot", request)
        return error_response(502, "Caller refresh failed", request)
    logger.info("openapi_caller_refresh_succeeded system=backend operation=refresh_caller_token fields=%s status=%s results=%s duration_ms=%.1f",
                fields, data.status, data.model_dump(), (time.monotonic() - started) * 1000)
    return envelope(data, request)
