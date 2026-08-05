"""Authenticated, opt-in Caller identity HTTP endpoints."""

from __future__ import annotations

import asyncio
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from agentclaw.community.adapters.http.auth.dependencies import get_current_user
from agentclaw.community.adapters.http.auth.models import AuthenticatedUser
from agentclaw.community.adapters.http.caller_identity.schemas import (
    CallerContextQuery,
    CallerContextResponse,
    UpdateMcpCallTypeQuery,
    UpdateMcpCallTypeRequest,
    UpdateMcpCallTypeResponse,
)
from agentclaw.community.api.caller_identity_service import (
    CallerIdentityServiceProtocol,
)
from agentclaw.community.core.errors import DomainError, InternalError
from agentclaw.community.di import Injected
from agentclaw.community.log import get_logger


logger = get_logger()

router = APIRouter(prefix="/api/bots", tags=["caller-identity"])


@router.get(
    "/{bot_id}/caller-context",
    response_model=CallerContextResponse,
)
async def get_caller_context(
    bot_id: str,
    query: Annotated[CallerContextQuery, Query()],
    user: AuthenticatedUser = Depends(get_current_user),
    service: CallerIdentityServiceProtocol = Injected(CallerIdentityServiceProtocol),
) -> CallerContextResponse:
    """Return exact draft or published Caller identity context."""
    logger.info(
        "caller_context_get_started bot_id=%s stage=%s publish_id=%s entity_scoped=%s",
        bot_id,
        query.stage.value,
        query.publish_id,
        query.entity_id is not None,
    )
    # COSEC: the actor is derived only from the authenticated request context;
    # query/path values cannot replace the current identity.
    try:
        result = await asyncio.to_thread(
            service.get_context,
            bot_id=bot_id,
            actor_id=user.staffId,
            stage=query.stage,
            publish_id=query.publish_id,
            entity_id=query.entity_id,
        )
    except DomainError:
        logger.warning(
            "caller_context_get_failed bot_id=%s stage=%s publish_id=%s entity_scoped=%s",
            bot_id,
            query.stage.value,
            query.publish_id,
            query.entity_id is not None,
        )
        raise
    except Exception:
        logger.warning(
            "caller_context_get_failed bot_id=%s stage=%s publish_id=%s entity_scoped=%s",
            bot_id,
            query.stage.value,
            query.publish_id,
            query.entity_id is not None,
        )
        # COSEC: replace untrusted exception details and suppress their context
        # before the production handler logs or serializes this failure.
        raise InternalError("CALLER_IDENTITY_INTERNAL_ERROR") from None
    response = CallerContextResponse(
        capability=result.capability,
        stage=result.stage,
        publish_id=result.publish_id,
        bot_call_type=result.bot_call_type,
        mcp_call_types=dict(result.mcp_call_types),
        editable=result.editable,
    )
    logger.info(
        "caller_context_get_succeeded bot_id=%s stage=%s publish_id=%s "
        "entity_scoped=%s bot_call_type=%s",
        bot_id,
        response.stage.value,
        response.publish_id,
        query.entity_id is not None,
        response.bot_call_type.value,
    )
    return response


@router.patch(
    "/{bot_id}/mcps/{server_code}/call-type",
    response_model=UpdateMcpCallTypeResponse,
)
async def update_mcp_call_type(
    bot_id: str,
    server_code: str,
    request: UpdateMcpCallTypeRequest,
    query: Annotated[UpdateMcpCallTypeQuery, Query()],
    user: AuthenticatedUser = Depends(get_current_user),
    service: CallerIdentityServiceProtocol = Injected(CallerIdentityServiceProtocol),
) -> UpdateMcpCallTypeResponse:
    """Update one draft MCP identity using the current authenticated owner."""
    logger.info(
        "mcp_call_type_http_update_started bot_id=%s stage=draft "
        "server_code=%s call_type=%s lock_epoch=%s entity_scoped=%s",
        bot_id,
        server_code,
        request.call_type.value,
        request.lock_epoch,
        query.entity_id is not None,
    )
    # COSEC: user/owner/engine identity is absent from the strict body and the
    # actor is always supplied from the authenticated request context.
    try:
        result = await service.update_mcp_call_type(
            bot_id=bot_id,
            server_code=server_code,
            call_type=request.call_type,
            actor_id=user.staffId,
            lock_epoch=request.lock_epoch,
            entity_id=query.entity_id,
        )
    except DomainError:
        logger.warning(
            "mcp_call_type_http_update_failed bot_id=%s stage=draft "
            "server_code=%s call_type=%s lock_epoch=%s entity_scoped=%s",
            bot_id,
            server_code,
            request.call_type.value,
            request.lock_epoch,
            query.entity_id is not None,
        )
        raise
    except Exception:
        logger.warning(
            "mcp_call_type_http_update_failed bot_id=%s stage=draft "
            "server_code=%s call_type=%s lock_epoch=%s entity_scoped=%s",
            bot_id,
            server_code,
            request.call_type.value,
            request.lock_epoch,
            query.entity_id is not None,
        )
        # COSEC: replace untrusted exception details and suppress their context
        # before the production handler logs or serializes this failure.
        raise InternalError("CALLER_IDENTITY_INTERNAL_ERROR") from None
    response = UpdateMcpCallTypeResponse(
        server_code=result.server_code,
        call_type=result.call_type,
        bot_call_type=result.bot_call_type,
    )
    logger.info(
        "mcp_call_type_http_update_succeeded bot_id=%s stage=draft "
        "server_code=%s call_type=%s bot_call_type=%s lock_epoch=%s entity_scoped=%s",
        bot_id,
        response.server_code,
        response.call_type.value,
        response.bot_call_type.value,
        request.lock_epoch,
        query.entity_id is not None,
    )
    return response


__all__ = ["router"]
