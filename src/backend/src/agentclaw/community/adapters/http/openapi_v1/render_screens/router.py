"""Public bot-first CRUD routes for render-screen CDN mappings."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Request

from agentclaw.community.adapters.http.openapi_v1.contracts import (
    BotIdPath,
    Deleted,
    Envelope,
)
from agentclaw.community.adapters.http.openapi_v1.engine_runtime.params import (
    OwnerIdDep,
)
from agentclaw.community.adapters.http.openapi_v1.principal import (
    UserIdDep,
    refuse_app_only_caller,
)
from agentclaw.community.adapters.http.openapi_v1.responses import (
    created,
    deleted,
    envelope,
    envelope_errors,
)
from agentclaw.community.api.bot_service import BotServiceProtocol
from agentclaw.community.api.collaborator_service import CollaboratorServiceProtocol
from agentclaw.community.api.render_screen_service import RenderScreenServiceProtocol
from agentclaw.community.core.bot_management.render_screen.errors import (
    RenderScreenConflictError,
    RenderScreenNotFoundError,
)
from agentclaw.community.core.bot_management.render_screen.models import (
    RenderScreenRecord,
)
from agentclaw.community.di import Injected

from .gating import require_editable_bot, require_scoped_record, resolve_readable_bot
from .schemas import (
    RenderScreen,
    RenderScreenCreate,
    RenderScreenList,
    RenderScreenUpdate,
)


router = APIRouter(
    prefix="/openapi/v1/bots/{bot_id}/render-screens",
    tags=["render-screens"],
)
_REFUSES_APP_ONLY = [Depends(refuse_app_only_caller)]
RenderScreenIdPath = Annotated[
    int,
    Path(
        ge=1,
        description="Render-screen mapping identifier returned by this collection.",
    ),
]


async def resolve_render_screen_write_owner_id(
    actor_id: UserIdDep,
    owner_id: Annotated[
        str | None,
        Query(
            min_length=1,
            description="Owner of the Bot whose render-screen configuration is addressed. "
            "Defaults to the current user; editors name the Bot Owner.",
        ),
    ] = None,
) -> str:
    """Resolve the owner for a human-only render-screen mutation."""
    return owner_id if owner_id is not None else actor_id


RenderScreenWriteOwnerIdDep = Annotated[
    str, Depends(resolve_render_screen_write_owner_id)
]


def _screen(record: RenderScreenRecord) -> RenderScreen:
    return RenderScreen(
        id=record.id,
        name=record.name,
        cdn_url=record.cdn_url,
        creator_id=record.creator_id,
        created_at=record.gmt_create,
        updated_at=record.gmt_modified,
    )


@router.get("", response_model=Envelope[RenderScreenList])
@envelope_errors
async def list_render_screens(
    bot_id: BotIdPath,
    request: Request,
    actor_id: UserIdDep,
    owner_id: OwnerIdDep,
    bot_service: BotServiceProtocol = Injected(BotServiceProtocol),
    service: RenderScreenServiceProtocol = Injected(RenderScreenServiceProtocol),
) -> Envelope[RenderScreenList]:
    """List the non-sensitive CDN mappings needed to render this Bot's panels."""
    resolve_readable_bot(bot_service, bot_id=bot_id, owner_id=owner_id)
    records = service.list_render_screens(bot_id=bot_id, owner_id=owner_id)
    items = [_screen(record) for record in records]
    return envelope(RenderScreenList(total=len(items), items=items), request)


@router.post(
    "",
    status_code=201,
    response_model=Envelope[RenderScreen],
    dependencies=_REFUSES_APP_ONLY,
)
@envelope_errors
async def create_render_screen(
    bot_id: BotIdPath,
    body: RenderScreenCreate,
    request: Request,
    actor_id: UserIdDep,
    owner_id: RenderScreenWriteOwnerIdDep,
    bot_service: BotServiceProtocol = Injected(BotServiceProtocol),
    collaborators: CollaboratorServiceProtocol = Injected(
        CollaboratorServiceProtocol
    ),
    service: RenderScreenServiceProtocol = Injected(RenderScreenServiceProtocol),
) -> Envelope[RenderScreen]:
    """Add a CDN mapping; Bot Owner or live Editor Member access is required."""
    require_editable_bot(
        bot_service,
        collaborators,
        bot_id=bot_id,
        owner_id=owner_id,
        actor_id=actor_id,
    )
    try:
        record_id = service.create_render_screen(
            bot_id=bot_id,
            owner_id=owner_id,
            name=body.name,
            cdn_url=str(body.cdn_url),
            creator_id=actor_id,
        )
    except ValueError as exc:
        raise RenderScreenConflictError("render screen conflict") from exc
    record = require_scoped_record(
        service,
        record_id=record_id,
        bot_id=bot_id,
        owner_id=owner_id,
    )
    return created(_screen(record), request)


@router.patch(
    "/{render_screen_id}",
    response_model=Envelope[RenderScreen],
    dependencies=_REFUSES_APP_ONLY,
)
@envelope_errors
async def update_render_screen(
    bot_id: BotIdPath,
    render_screen_id: RenderScreenIdPath,
    body: RenderScreenUpdate,
    request: Request,
    actor_id: UserIdDep,
    owner_id: RenderScreenWriteOwnerIdDep,
    bot_service: BotServiceProtocol = Injected(BotServiceProtocol),
    collaborators: CollaboratorServiceProtocol = Injected(
        CollaboratorServiceProtocol
    ),
    service: RenderScreenServiceProtocol = Injected(RenderScreenServiceProtocol),
) -> Envelope[RenderScreen]:
    """Replace one mapping after binding its id to the addressed Bot."""
    require_editable_bot(
        bot_service,
        collaborators,
        bot_id=bot_id,
        owner_id=owner_id,
        actor_id=actor_id,
    )
    require_scoped_record(
        service,
        record_id=render_screen_id,
        bot_id=bot_id,
        owner_id=owner_id,
    )
    try:
        service.update_render_screen(
            record_id=render_screen_id,
            name=body.name,
            cdn_url=str(body.cdn_url),
        )
    except ValueError as exc:
        raise RenderScreenNotFoundError("render screen not found") from exc
    updated = require_scoped_record(
        service,
        record_id=render_screen_id,
        bot_id=bot_id,
        owner_id=owner_id,
    )
    return envelope(_screen(updated), request)


@router.delete(
    "/{render_screen_id}",
    response_model=Envelope[Deleted],
    dependencies=_REFUSES_APP_ONLY,
)
@envelope_errors
async def delete_render_screen(
    bot_id: BotIdPath,
    render_screen_id: RenderScreenIdPath,
    request: Request,
    actor_id: UserIdDep,
    owner_id: RenderScreenWriteOwnerIdDep,
    bot_service: BotServiceProtocol = Injected(BotServiceProtocol),
    collaborators: CollaboratorServiceProtocol = Injected(
        CollaboratorServiceProtocol
    ),
    service: RenderScreenServiceProtocol = Injected(RenderScreenServiceProtocol),
) -> Envelope[Deleted]:
    """Soft-delete one mapping after Bot and record authorization."""
    require_editable_bot(
        bot_service,
        collaborators,
        bot_id=bot_id,
        owner_id=owner_id,
        actor_id=actor_id,
    )
    require_scoped_record(
        service,
        record_id=render_screen_id,
        bot_id=bot_id,
        owner_id=owner_id,
    )
    try:
        service.delete_render_screen(record_id=render_screen_id)
    except ValueError as exc:
        raise RenderScreenNotFoundError("render screen not found") from exc
    return deleted(request)


__all__ = [
    "RenderScreenWriteOwnerIdDep",
    "create_render_screen",
    "delete_render_screen",
    "list_render_screens",
    "resolve_render_screen_write_owner_id",
    "router",
    "update_render_screen",
]
