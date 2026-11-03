"""Canonicalize historical BFF locators without changing the wire contract."""

from __future__ import annotations

from agentclaw.community.adapters.http.dependencies import RequestContext
from agentclaw.community.core.bot_management.addressing import BotAddressResolver
from agentclaw.community.core.bot_management.engines.registry import resolve_bot_engine
from agentclaw.community.core.bot_management.errors import BotAddressNotFoundError
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.workspace.constants import DEFAULT_ENGINE_TYPE


def legacy_bot(bot_repo: BotRepository, bot_id: str, locator: str) -> dict | None:
    """Resolve a compatibility locator; absence retains existing detached flows.

    This is addressing, never authorization. Ambiguity and persistence failures
    propagate. Callers that require a live Bot must reject a missing result.
    """
    try:
        return dict(
            BotAddressResolver(bot_repo)
            .resolve(bot_id=bot_id, legacy_owner_id=locator)
            .bot
        )
    except BotAddressNotFoundError:
        return None


def legacy_path_params(ctx: RequestContext, entity_id, entity_type, bot_id, engine_type, *, bot_repo):
    # Direct router unit calls may still carry FastAPI Query defaults.
    entity_id = entity_id if isinstance(entity_id, str) else None
    entity_type = entity_type if isinstance(entity_type, str) else None
    bot_id = bot_id if isinstance(bot_id, str) else None
    engine_type = engine_type if isinstance(engine_type, str) else None
    locator = entity_id or ctx.user_id or "default"
    target = bot_id or ctx.bot_id or "default"
    bot = legacy_bot(bot_repo, target, locator)
    if bot is None:
        # Historical global catalog/default and detached paths do not establish
        # access to a live Bot. Their operation-specific ACL remains in force.
        data_engine = engine_type or DEFAULT_ENGINE_TYPE
        return locator, target, data_engine, data_engine, entity_type or "staff", False
    resource_entity = str(bot["entity_id"])
    if not resource_entity.strip():
        raise ValueError("Bot entity_id is required for resource addressing")
    data_engine = engine_type or bot.get("active_engine") or DEFAULT_ENGINE_TYPE
    routed = dict(bot, active_engine=data_engine)
    return (
        resource_entity,
        target,
        data_engine,
        resolve_bot_engine(routed) or data_engine,
        entity_type or bot.get("entity_type") or "staff",
        bot.get("bot_type") == "desktop",
    )
