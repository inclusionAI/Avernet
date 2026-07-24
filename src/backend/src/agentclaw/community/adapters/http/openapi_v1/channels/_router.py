"""Channels group — ``/openapi/v1/channels`` endpoints (definition only).

Channel config (DingTalk in v1) CRUD + status toggle. Handlers are stubs; every
route requires an authenticated user principal.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from agentclaw.community.adapters.http.openapi_v1._deps import require_principal
from agentclaw.community.adapters.http.openapi_v1._contracts import (
    Deleted,
    Envelope,
    requires_user_principal,
)
from agentclaw.community.adapters.http.openapi_v1._deps import Principal

from ._schemas import Channel, ChannelCreate, ChannelStatusUpdate, ChannelUpdate

router = APIRouter(prefix="/openapi/v1/bots/channels", tags=["channels"])

_SEC = requires_user_principal()
PrincipalDep = Annotated[Principal, Depends(require_principal)]


@router.get("", response_model=Envelope[list[Channel]], openapi_extra=_SEC)
async def list_channels(
    principal: PrincipalDep, bot_id: str | None = None
) -> Envelope[list[Channel]]:
    """List channels (optionally filtered by bot)."""
    raise NotImplementedError


@router.post("", status_code=201, response_model=Envelope[Channel], openapi_extra=_SEC)
async def create_channel(
    body: ChannelCreate, principal: PrincipalDep
) -> Envelope[Channel]:
    """Create a channel (starts inactive)."""
    raise NotImplementedError


@router.get("/{channel_id}", response_model=Envelope[Channel], openapi_extra=_SEC)
async def get_channel(channel_id: str, principal: PrincipalDep) -> Envelope[Channel]:
    """Get a channel."""
    raise NotImplementedError


@router.put("/{channel_id}", response_model=Envelope[Channel], openapi_extra=_SEC)
async def update_channel(
    channel_id: str, body: ChannelUpdate, principal: PrincipalDep
) -> Envelope[Channel]:
    """Full update of a channel."""
    raise NotImplementedError


@router.patch("/{channel_id}", response_model=Envelope[Channel], openapi_extra=_SEC)
async def update_channel_status(
    channel_id: str, body: ChannelStatusUpdate, principal: PrincipalDep
) -> Envelope[Channel]:
    """Toggle a channel active/inactive."""
    raise NotImplementedError


@router.delete("/{channel_id}", response_model=Envelope[Deleted], openapi_extra=_SEC)
async def delete_channel(channel_id: str, principal: PrincipalDep) -> Envelope[Deleted]:
    """Delete a channel."""
    raise NotImplementedError
