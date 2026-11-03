"""Resolve and authorize the same Bot addressed by a risk-check request."""
from __future__ import annotations

from typing import Literal

from agentclaw.community.core.bot_collaborator.models import PermissionLevel
from agentclaw.community.core.bot_collaborator.protocols import (
    CollaboratorServiceProtocol, resolve_explicit_permission_level,
)
from agentclaw.community.core.bot_management.addressing import BotAddressResolver, ResolvedBotContext
from agentclaw.community.core.bot_management.errors import (
    BotAddressConflictError, BotAddressNotFoundError, BotLookupAmbiguousError,
)
from agentclaw.community.core.repository.protocols.bot import BotRepository


class RiskAccessDenied(LookupError):
    """The caller cannot manage the addressed Bot; do not reveal its existence."""


def authorize_risk_bot(
    bots: BotRepository,
    collaborators: CollaboratorServiceProtocol,
    *,
    bot_id: str,
    actor_id: str,
    surface: Literal["legacy", "openapi"],
    legacy_owner_id: str | None = None,
) -> ResolvedBotContext:
    if not actor_id or actor_id == "anonymous":
        raise RiskAccessDenied("Bot unavailable")
    resolver = BotAddressResolver(bots)
    try:
        if surface == "openapi":
            bare_id, separator, entity_id = bot_id.rpartition(":")
            if not separator or not bare_id or not entity_id:
                raise RiskAccessDenied("Bot unavailable")
            resolved = resolver.resolve(bot_id=bare_id, entity_id=entity_id)
        elif surface == "legacy":
            resolved = resolver.resolve(bot_id=bot_id, legacy_owner_id=legacy_owner_id or actor_id)
        else:
            raise RiskAccessDenied("Bot unavailable")
    except (BotAddressNotFoundError, BotAddressConflictError, BotLookupAmbiguousError) as exc:
        raise RiskAccessDenied("Bot unavailable") from exc
    level = resolve_explicit_permission_level(
        collaborators, bot=resolved.bot, user_id=actor_id,
        owner_id=resolved.current_owner_id, env=resolved.bot.get("env"),
    )
    if level < PermissionLevel.ADMIN:
        raise RiskAccessDenied("Bot unavailable")
    return resolved
