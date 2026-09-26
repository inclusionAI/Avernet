"""New-version publish-to-users endpoint (BCS-delegated, backend-served).

Served path: POST /openapi/v1/collaboration/bots/{bot_uuid}/public  (contract path)

The gateway exposes this address and its collaboration-publish domain
verbatim-forwards it here (origin swap only, no rewrite), so the backend
serves the public address directly rather than relying on a gateway path
rewrite. bot_uuid is the BCS bot identity (backend_bot_id:entity_id); the
service splits it internally (backend_bot_id = bot_uid.split(":")[0]).

Starts a botpublish approval ticket whose context carries the normal-bot
publishHint/botSkills/botMcps (built by _build_public_approval_context) plus
public_scope, viewFriendDeps, and the stored visibility target.

Auth (grant / admission) is deferred for now — the handler identifies the
caller (owner/operator) but applies no bot-grant check.
"""
from __future__ import annotations

from fastapi import APIRouter, Request

from agentclaw.community.adapters.http.openapi_v1.contracts import BotIdPath, Envelope
from agentclaw.community.adapters.http.openapi_v1.principal import UserIdDep
from agentclaw.community.adapters.http.openapi_v1.responses import (
    envelope,
    envelope_errors,
)
from agentclaw.community.api.bot_public_service import BotPublicServiceProtocol
from agentclaw.community.core.operator_context import OperatorContext
from agentclaw.community.di import Injected

from .schemas import BcsPublicRequest, BcsPublishResult
from agentclaw.community.adapters.http.openapi_v1.authorization import PublicAPIRoute

public_router = APIRouter(prefix="/openapi/v1/collaboration/bots", tags=["collaboration-bots"], route_class=PublicAPIRoute)


@public_router.post("/{bot_uuid}/public", response_model=Envelope[BcsPublishResult])
@envelope_errors
async def publish_bcs_bot_external(
    bot_uuid: BotIdPath,
    actor_id: UserIdDep,
    request: Request,
    req: BcsPublicRequest,
    service: BotPublicServiceProtocol = Injected(BotPublicServiceProtocol),
) -> Envelope[BcsPublishResult]:
    """Publish a bot to users or agents — opens a botpublish approval ticket.

    bot_uuid is the BCS bot identity the gateway forwards verbatim; the service
    splits it internally to reverse-lookup the backend bot record.
    """
    operator = OperatorContext(
        staff_id=actor_id,
        staff=actor_id,
        nick_name=actor_id,
        operator_name=actor_id,
        tenant_id="default",
    )
    result = service.public_bcs_bot(
        bot_uid=bot_uuid,
        owner_id=actor_id,
        public_scope=req.public_scope,
        view_depts=[dept.model_dump() for dept in req.view_depts] if req.view_depts else None,
        visibility=req.visibility,
        operator=operator,
    )
    return envelope(BcsPublishResult.model_validate(result), request)
