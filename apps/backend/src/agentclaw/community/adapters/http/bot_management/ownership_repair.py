"""Authenticated operations admission, never a remote delivery receipt."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from agentclaw.community.adapters.http.auth.dependencies import get_current_user
from agentclaw.community.adapters.http.auth.models import AuthenticatedUser
from agentclaw.community.core.bot_management.ownership_sync.operations import (
    OwnershipRepairError,
    OwnershipSyncOperations,
)
from agentclaw.community.di import Injected

router = APIRouter(tags=["bot-owner-handoff"])


class RepairOwnership(BaseModel):
    model_config = ConfigDict(extra="forbid")
    bot_pk: int = Field(gt=0)


@router.post("/owner-handoffs/bcn-repair", status_code=202)
def repair_ownership(
    body: RepairOwnership,
    user: AuthenticatedUser = Depends(get_current_user),
    service: OwnershipSyncOperations = Injected(OwnershipSyncOperations),
):
    try:
        return {
            "success": True,
            "data": service.request_repair(
                bot_pk=body.bot_pk,
                actor_id=user.staffId,
            ),
        }
    except OwnershipRepairError as exc:
        status = {
            "ADMIN_REQUIRED": 403,
            "BOT_NOT_FOUND": 404,
            "SERVICE_BOT_REQUIRED": 409,
            "INVALID_BCN_ADDRESS": 409,
        }.get(str(exc), 503)
        raise HTTPException(status_code=status, detail=str(exc)) from exc
