"""Internal endpoint: BCS calls this after resolving a human→bot friend relation.

Auth: ``Depends(require_user_caller)`` — forwarded gateway principal, same seam
as ``/api/v1/work-orders/events``. agent_code is resolved INSIDE the service
(via resolve_agent_code) using the bot_id + owner_work_no from the body.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from agentclaw.community.adapters.http.bot_friend_auth.schemas import (
    FriendAuthSyncRequest,
    FriendAuthSyncResponse,
)
from agentclaw.community.adapters.http.org.dependencies import require_user_caller
from agentclaw.community.api.bot_friend_auth_service import FriendAuthSyncServiceProtocol
from agentclaw.community.core.bot_public.services.friend_auth_sync_service import (
    AgentCodeUnavailableError,
    AuthRelationshipSyncError,
    BotNotFoundError,
    FriendAuthSyncError,
)
from agentclaw.community.core.gateway_principal import VerifiedCaller
from agentclaw.community.di import Injected
from agentclaw.community.log import get_logger

logger = get_logger()

router = APIRouter(prefix="/api/internal/bot-friend-auth", tags=["bot-friend-auth-internal"])


@router.post("/sync", response_model=FriendAuthSyncResponse)
async def sync_friend_auth(
    body: FriendAuthSyncRequest,
    caller: VerifiedCaller = Depends(require_user_caller),
    service: FriendAuthSyncServiceProtocol = Injected(FriendAuthSyncServiceProtocol),
) -> FriendAuthSyncResponse:
    logger.info(
        "[bot_friend_auth] sync caller=%s bot_id=%s action=%s request_id=%s",
        caller.user_id, body.bot_id, body.action.value, body.request_id,
    )
    try:
        result = service.sync(
            bot_id=body.bot_id,
            owner_work_no=body.owner_work_no,
            human_work_no=body.human_work_no,
            action=body.action.value,
        )
    except BotNotFoundError:
        raise HTTPException(status_code=400, detail="bot not found")
    except AgentCodeUnavailableError:
        raise HTTPException(status_code=400, detail="agent_code unavailable")
    except AuthRelationshipSyncError:
        raise HTTPException(status_code=502, detail="agentpass sync failed")
    except FriendAuthSyncError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return FriendAuthSyncResponse(**result)
