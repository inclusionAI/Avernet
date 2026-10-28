"""Resolve a fully qualified Bot address and its application grant once."""

from typing import Annotated

import asyncio
from fastapi import Depends, Request

from agentclaw.community.adapters.http.openapi_v1.contracts import BotIdPath
from agentclaw.community.adapters.http.openapi_v1.errors import BotAccessRefusedError
from agentclaw.community.adapters.http.openapi_v1.principal import ActingCallerDep
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
