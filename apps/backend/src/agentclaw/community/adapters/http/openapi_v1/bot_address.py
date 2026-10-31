"""Resolve collaboration addresses and legacy Bot locators for OpenAPI callers."""

from typing import Annotated

import asyncio
from fastapi import Depends, Request

from agentclaw.community.adapters.http.openapi_v1.contracts import BotIdPath
from agentclaw.community.adapters.http.openapi_v1.errors import BotAccessRefusedError
from agentclaw.community.adapters.http.openapi_v1.principal import (
    ActingCallerDep,
    ENTITY_ID_QUERY,
)
from agentclaw.community.core.bot_management.addressing import (
    BotAddressResolver,
    ResolvedBotContext,
)
from agentclaw.community.core.bot_management.errors import (
    BotAddressNotFoundError,
    BotAddressConflictError,
    BotLookupAmbiguousError,
)
from agentclaw.community.core.repository.protocols.bot import BotRepository


async def resolve_collaboration_bot(
    request: Request, bot_id: BotIdPath
) -> ResolvedBotContext:
    """Resolve the path's bot_id:entity_id without treating the actor as owner."""
    bare_id, separator, entity_id = bot_id.rpartition(":")
    if not separator or not bare_id or not entity_id:
        raise BotAccessRefusedError("Bot address unavailable")
    try:
        bots = request.app.state.injector.get(BotRepository)
        return await asyncio.to_thread(
            BotAddressResolver(bots).resolve, bot_id=bare_id, entity_id=entity_id
        )
    except (BotAddressNotFoundError, BotAddressConflictError, BotLookupAmbiguousError):
        raise BotAccessRefusedError("Bot address unavailable") from None


async def require_granted_collaboration_bot(
    caller: ActingCallerDep,
    bot: ResolvedBotContext = Depends(resolve_collaboration_bot),
) -> ResolvedBotContext:
    """Apply the existing addressed-Bot grant to the canonical path identity.

    A human passes this grant seam unchanged; the authorization table still
    decides their collaborator level. Applications must hold the existing live
    Bot grant from the delegating user, scoped to the addressed entity.
    """
    await asyncio.to_thread(caller.require_bot, bot.bot_id, owner_id=bot.entity_id)
    return bot


CollaborationBotDep = Annotated[
    ResolvedBotContext, Depends(require_granted_collaboration_bot)
]


async def resolve_bot_entity(request: Request, *, bot_id: str, locator: str) -> str:
    """Keep legacy Owner locators out of entity-only Core service arguments.

    ``locator`` must come from OwnerIdDep, after its principal/grant checks.
    Explicit entity addresses stay strict; only the legacy owner spelling (or
    the authenticated user's default) uses owner/entity compatibility lookup.
    This identifies the Bot; the route and Core service retain authorization.
    """
    repository = request.app.state.injector.get(BotRepository)
    resolver = BotAddressResolver(repository)
    try:
        context = await asyncio.to_thread(
            resolver.resolve,
            bot_id=bot_id,
            **(
                {"entity_id": locator}
                if request.query_params.get(ENTITY_ID_QUERY)
                else {"legacy_owner_id": locator}
            ),
        )
    except BotAddressNotFoundError as exc:
        raise BotAccessRefusedError("Bot not found") from exc
    return context.entity_id
