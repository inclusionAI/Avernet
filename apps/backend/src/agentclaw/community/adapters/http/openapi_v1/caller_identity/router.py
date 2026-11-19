"""Public Caller identity context and per-MCP identity configuration."""

from __future__ import annotations

import asyncio
from typing import Annotated

from fastapi import APIRouter, Path, Query, Request

from agentclaw.community.adapters.http.openapi_v1.authorization import PublicAPIRoute
from agentclaw.community.adapters.http.openapi_v1.bot_address import resolve_bot_entity
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
    CALLER_IDENTITY_CAPABILITY,
    CallerContext as CoreCallerContext,
    CallerIdentityPermissionError,
    CallerIdentityServiceProtocol,
    CallerIdentityStage as CoreCallerIdentityStage,
    McpCallType as CoreMcpCallType,
)
from agentclaw.community.api.human_bot_friendship_service import (
    HumanBotFriendshipServiceProtocol,
)
from agentclaw.community.api.collaborator_lock_service import (
    CollaboratorLockServiceProtocol,
)
from agentclaw.community.di import Injected
from agentclaw.community.log import get_logger

from .schemas import (
    CallerCallType,
    CallerContext,
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
    friendships: HumanBotFriendshipServiceProtocol = Injected(
        HumanBotFriendshipServiceProtocol
    ),
) -> Envelope[CallerContext]:
    """Return aggregate and per-resource Caller overrides for one Bot runtime."""
    _validate_stage(stage, publish_id)
    entity_id = await resolve_bot_entity(request, bot_id=bot_id, locator=owner_id)
    core_stage = CoreCallerIdentityStage(stage.value)
    try:
        result = await asyncio.to_thread(
            service.get_context,
            bot_id=bot_id,
            actor_id=user_id,
            stage=core_stage,
            publish_id=publish_id,
            entity_id=entity_id,
        )
    except CallerIdentityPermissionError as denied:
        try:
            is_friend = await asyncio.to_thread(
                friendships.is_friend,
                human_id=user_id,
                bot_id=bot_id,
                owner_id=entity_id,
                request_headers={
                    key: value
                    for key, value in request.headers.items()
                    if key.lower()
                    in {"authorization", "cookie", "x-request-id", "x-trace-id"}
                },
            )
        except Exception as error:  # noqa: BLE001 - authorization must fail closed
            logger.warning(
                "openapi_caller_context_friendship_lookup_failed "
                "bot_id=%s actor_id=%s error_type=%s",
                bot_id,
                user_id,
                type(error).__name__,
            )
            raise denied from None
        if not is_friend:
            raise denied
        bot_call_type = await asyncio.to_thread(
            service.get_bot_call_type,
            bot_id,
            core_stage,
            publish_id,
            entity_id,
        )
        result = CoreCallerContext(
            capability=CALLER_IDENTITY_CAPABILITY,
            stage=core_stage,
            publish_id=publish_id,
            bot_call_type=bot_call_type,
            mcp_call_types={},
            cli_call_types={},
            editable=False,
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
