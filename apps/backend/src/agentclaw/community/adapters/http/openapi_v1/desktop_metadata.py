"""Avatar writes and link resources for owner-managed Bots."""

import json
from typing import Annotated, Any, Literal

from agentclaw.community.adapters.http.openapi_v1.authorization import PublicAPIRoute
from agentclaw.community.adapters.http.openapi_v1.contracts import (
    BotIdPath,
    Deleted,
    Envelope,
)
from agentclaw.community.adapters.http.openapi_v1.principal import (
    UserIdDep,
    require_granted_own_bot,
)
from agentclaw.community.adapters.http.openapi_v1.responses import (
    deleted,
    envelope,
    envelope_errors,
)
from agentclaw.community.api.bot_service import BotServiceProtocol
from agentclaw.community.api.link_workflow_service import LinkWorkflowProtocol
from agentclaw.community.core.resources.yuque_resolve import YuqueResolveError
from agentclaw.community.di import Injected
from fastapi import APIRouter, Depends, HTTPException, Path, Request
from pydantic import BaseModel, ConfigDict, Field

router = APIRouter(
    prefix="/openapi/v1/bots",
    tags=["bot-metadata"],
    route_class=PublicAPIRoute,
    dependencies=[Depends(require_granted_own_bot)],
)


class AvatarWrite(BaseModel):
    """Replace the avatar; an empty URL clears it."""

    model_config = ConfigDict(extra="forbid")
    avatar_url: str = Field(
        description="Avatar URL supplied by the existing upload or generation channel."
    )


class LinkWrite(BaseModel):
    """One external knowledge link."""

    model_config = ConfigDict(extra="forbid")
    link_type: Literal["yuque", "dima", "antcode"] = Field(
        description="Existing knowledge source type."
    )
    url: str = Field(min_length=1, description="Source URL to resolve and associate.")
    name: str = Field(
        default="", description="Display name; empty uses the resolved title or URL."
    )
    access_modes: list[Literal["READ", "WRITE"]] = Field(
        default_factory=lambda: ["READ"], description="Yuque resource permissions."
    )


class LinkBatch(BaseModel):
    """Batch of links; duplicates are refused before persistence."""

    model_config = ConfigDict(extra="forbid")
    links: list[LinkWrite] = Field(
        min_length=1, description="Links to add to this Bot."
    )


class LinkPatch(BaseModel):
    """Partial link update; omitted fields retain their values."""

    model_config = ConfigDict(extra="forbid")
    name: str = Field(default=None, description="New display name; omit to retain.")
    url: str = Field(default=None, min_length=1, description="New URL; omit to retain.")
    link_type: Literal["yuque", "dima", "antcode"] = Field(
        default=None, description="New source type; omit to retain."
    )
    access_modes: list[Literal["READ", "WRITE"]] = Field(
        default=None, description="New Yuque permissions; omit to retain."
    )


def _link(resource) -> dict[str, Any]:
    return {"id": str(resource.id), "name": resource.name, **resource.attributes}


@router.put("/{bot_id}/avatar", response_model=Envelope[AvatarWrite])
@envelope_errors
async def update_avatar(
    bot_id: BotIdPath,
    owner_id: UserIdDep,
    body: AvatarWrite,
    request: Request,
    bots: BotServiceProtocol = Injected(BotServiceProtocol),
):
    """Update avatar through the existing metadata/BCN synchronization service."""
    bots.update_bot(
        bot_id,
        owner_id,
        ext={"avatar_url": body.avatar_url},
        request_headers={
            key: value
            for key, value in request.headers.items()
            if key.lower() == "authorization"
        },
    )
    return envelope(body, request)


@router.get("/{bot_id}/links", response_model=Envelope[list[dict[str, Any]]])
@envelope_errors
async def list_links(
    bot_id: BotIdPath,
    owner_id: UserIdDep,
    request: Request,
    bots: BotServiceProtocol = Injected(BotServiceProtocol),
    links: LinkWorkflowProtocol = Injected(LinkWorkflowProtocol),
):
    """List links in this owner's Bot; filesystem resources remain separate."""
    bots.get_bot(bot_id, owner_id)
    return envelope([_link(item) for item in links.list(bot_id, owner_id)], request)


@router.post("/{bot_id}/links", response_model=Envelope[list[dict[str, Any]]])
@envelope_errors
async def create_links(
    bot_id: BotIdPath,
    owner_id: UserIdDep,
    body: LinkBatch,
    request: Request,
    bots: BotServiceProtocol = Injected(BotServiceProtocol),
    links: LinkWorkflowProtocol = Injected(LinkWorkflowProtocol),
):
    """Resolve and create knowledge links with permission synchronization."""
    bots.get_bot(bot_id, owner_id)
    try:
        items = await links.create(
            bot_id, owner_id, [link.model_dump() for link in body.links]
        )
    except YuqueResolveError as exc:
        raise HTTPException(502, detail=exc.message) from exc
    return envelope([_link(item) for item in items], request)


@router.put("/{bot_id}/links/{resource_id}", response_model=Envelope[dict[str, Any]])
@envelope_errors
async def update_link(
    bot_id: BotIdPath,
    resource_id: Annotated[str, Path(description="Stored link resource identifier.")],
    owner_id: UserIdDep,
    body: LinkPatch,
    request: Request,
    bots: BotServiceProtocol = Injected(BotServiceProtocol),
    links: LinkWorkflowProtocol = Injected(LinkWorkflowProtocol),
):
    """Update an owner-scoped link and its Yuque permissions."""
    bots.get_bot(bot_id, owner_id)
    try:
        item = await links.update(
            bot_id, owner_id, resource_id, body.model_dump(exclude_unset=True)
        )
    except YuqueResolveError as exc:
        raise HTTPException(502, detail=exc.message) from exc
    return envelope(_link(item), request)


@router.delete("/{bot_id}/links/{resource_id}", response_model=Envelope[Deleted])
@envelope_errors
async def delete_link(
    bot_id: BotIdPath,
    resource_id: Annotated[str, Path(description="Stored link resource identifier.")],
    owner_id: UserIdDep,
    request: Request,
    bots: BotServiceProtocol = Injected(BotServiceProtocol),
    links: LinkWorkflowProtocol = Injected(LinkWorkflowProtocol),
):
    """Delete an owner-scoped link and synchronize remaining permissions."""
    bots.get_bot(bot_id, owner_id)
    await links.delete(bot_id, owner_id, resource_id)
    return deleted(request)


@router.get("/{bot_id}/avatar", response_model=Envelope[AvatarWrite])
@envelope_errors
async def get_avatar(
    bot_id: BotIdPath,
    owner_id: UserIdDep,
    request: Request,
    bots: BotServiceProtocol = Injected(BotServiceProtocol),
):
    """Read the existing avatar metadata, including legacy extension storage."""
    bot = bots.get_bot(bot_id, owner_id)
    ext = bot.get("ext") or {}
    if isinstance(ext, str):
        ext = json.loads(ext)
    return envelope(
        AvatarWrite(avatar_url=bot.get("avatar_url") or ext.get("avatar_url") or ""),
        request,
    )
