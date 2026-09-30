"""Public bot-first CRUD routes for render-screen CDN mappings."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Path, Request

from agentclaw.community.adapters.http.openapi_v1.contracts import (
    BotIdPath,
    Deleted,
    Envelope,
)
from agentclaw.community.adapters.http.openapi_v1.engine_runtime.params import (
    OwnerIdDep,
)
from agentclaw.community.adapters.http.openapi_v1.principal import UserIdDep
from agentclaw.community.adapters.http.openapi_v1.responses import (
    created,
    deleted,
    envelope,
    envelope_errors,
)
from agentclaw.community.api.bot_service import BotServiceProtocol
from agentclaw.community.api.render_screen_service import RenderScreenServiceProtocol
from agentclaw.community.core.bot_management.render_screen.errors import (
    RenderScreenConflictError,
    RenderScreenNotFoundError,
)
from agentclaw.community.core.bot_management.render_screen.models import (
    RenderScreenRecord,
)
from agentclaw.community.di import Injected

from .gating import require_scoped_record, resolve_readable_bot
from .schemas import (
    RenderScreen,
    RenderScreenCreate,
    RenderScreenList,
    RenderScreenUpdate,
)
from agentclaw.community.adapters.http.openapi_v1.authorization import PublicAPIRoute


router = APIRouter(
    prefix="/openapi/v1/bots/{bot_id}/render-screens",
    tags=["render-screens"],
    route_class=PublicAPIRoute,
)
RenderScreenIdPath = Annotated[
    int,
    Path(
        ge=1,
        description="Render-screen mapping identifier returned by this collection.",
    ),
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
    # COSEC: the only operation in this group that resolves the Bot itself, and
    # the only one whose row is ``NoCheck`` — share and group viewers hold no
    # Editor relation, so the seam deliberately adjudicates nothing here. That
    # makes this call the sole proof the addressed Bot exists under the named
    # owner; without it the read answers for any ``bot_id`` a caller can name.
    # ``actor_id`` is likewise load-bearing for its dependency rather than its
    # value: ``UserIdDep`` is what keeps this route authenticated-only.
    # The three mutations do not repeat it. They declare ``Check(MEMBER)``, so
    # ``bot_access._level`` has already resolved ``(bot_id, owner_id)`` and
    # refused on absence before the handler is entered.
    resolve_readable_bot(bot_service, bot_id=bot_id, owner_id=owner_id)
    records = service.list_render_screens(bot_id=bot_id, owner_id=owner_id, current_user_id=actor_id)
    items = [_screen(record) for record in records]
    return envelope(RenderScreenList(total=len(items), items=items), request)


@router.post(
    "",
    status_code=201,
    response_model=Envelope[RenderScreen],
)
@envelope_errors
async def create_render_screen(
    bot_id: BotIdPath,
    body: RenderScreenCreate,
    request: Request,
    actor_id: UserIdDep,
    owner_id: OwnerIdDep,
    service: RenderScreenServiceProtocol = Injected(RenderScreenServiceProtocol),
) -> Envelope[RenderScreen]:
    """Add a CDN mapping; Bot Owner or live Editor Member access is required."""
    try:
        record_id = service.create_render_screen(
            bot_id=bot_id,
            owner_id=owner_id,
            name=body.name,
            cdn_url=str(body.cdn_url),
            creator_id=actor_id,
            current_user_id=actor_id,
        )
    except ValueError as exc:
        raise RenderScreenConflictError("render screen conflict") from exc
    record = require_scoped_record(
        service,
        record_id=record_id,
        bot_id=bot_id,
        owner_id=owner_id,
        actor_id=actor_id,
    )
    return created(_screen(record), request)


@router.patch(
    "/{render_screen_id}",
    response_model=Envelope[RenderScreen],
)
@envelope_errors
async def update_render_screen(
    bot_id: BotIdPath,
    render_screen_id: RenderScreenIdPath,
    body: RenderScreenUpdate,
    request: Request,
    actor_id: UserIdDep,
    owner_id: OwnerIdDep,
    service: RenderScreenServiceProtocol = Injected(RenderScreenServiceProtocol),
) -> Envelope[RenderScreen]:
    """Replace one mapping after binding its id to the addressed Bot."""
    require_scoped_record(
        service,
        record_id=render_screen_id,
        bot_id=bot_id,
        owner_id=owner_id,
        actor_id=actor_id,
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
        actor_id=actor_id,
    )
    return envelope(_screen(updated), request)


@router.delete(
    "/{render_screen_id}",
    response_model=Envelope[Deleted],
)
@envelope_errors
async def delete_render_screen(
    bot_id: BotIdPath,
    render_screen_id: RenderScreenIdPath,
    request: Request,
    actor_id: UserIdDep,
    owner_id: OwnerIdDep,
    service: RenderScreenServiceProtocol = Injected(RenderScreenServiceProtocol),
) -> Envelope[Deleted]:
    """Soft-delete one mapping after Bot and record authorization."""
    require_scoped_record(
        service,
        record_id=render_screen_id,
        bot_id=bot_id,
        owner_id=owner_id,
        actor_id=actor_id,
    )
    try:
        service.delete_render_screen(record_id=render_screen_id)
    except ValueError as exc:
        raise RenderScreenNotFoundError("render screen not found") from exc
    return deleted(request)


__all__ = [
    "create_render_screen",
    "delete_render_screen",
    "list_render_screens",
    "router",
    "update_render_screen",
]
