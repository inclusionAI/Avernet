"""Current-user endpoint for JWT-authenticated ordinary HTTP clients."""

from __future__ import annotations

from typing import Annotated, cast

from fastapi import APIRouter, Depends

from agentclaw.community.adapters.http.org_user.dependencies import (
    require_gateway_user,
)
from agentclaw.community.adapters.http.org_user.schemas import OrgUserResponse
from agentclaw.community.core.gateway_principal import GatewayUser, VerifiedCaller

router = APIRouter(prefix="/api/v1/org", tags=["org"])

GatewayUserCaller = Annotated[VerifiedCaller, Depends(require_gateway_user)]


@router.get("/user", response_model=OrgUserResponse)
async def get_org_user(caller: GatewayUserCaller) -> OrgUserResponse:
    """Return the user identity asserted by the signed principal JWT."""
    user = cast(GatewayUser, caller.user)
    return OrgUserResponse(
        user_id=user.id,
        username=user.username,
        display_name=user.display_name,
        full_name=user.full_name,
    )
