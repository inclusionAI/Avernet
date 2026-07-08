"""Engine type resolution helper.

Frontend should not be required to know a bot's engine type — the backend
resolves it from `bot_id` per the multi-engine API design
(see docs/resource-management-api.md). API endpoints call this helper to
turn (bot_id, owner_id, optional override) into a concrete engine type.

Resolution order:
  1. Explicit override (operator/debug use); empty string treated as no override.
  2. Bot record's active_engine.
  3. DEFAULT_ENGINE_TYPE.
"""
from __future__ import annotations

from typing import Optional

from agentclaw.community.core.bot_management.repository.protocol import BotRepository
from agentclaw.community.core.workspace.constants import DEFAULT_ENGINE_TYPE
from agentclaw.community.log import get_logger

logger = get_logger()


def resolve_engine_for_bot(
    bot_id: Optional[str],
    owner_id: Optional[str] = None,
    override: Optional[str] = None,
    *,
    bot_repo: BotRepository,
) -> str:
    """Resolve the engine type for a bot.

    Args:
        bot_id: Bot identifier; if missing, only override / DEFAULT apply.
        owner_id: Bot owner (optional). If provided, uses get_by_id_and_owner
            for exact match; if not provided, uses get_by_id for broader match.
        override: Explicit engine_type from caller (e.g. ?engine_type=...).
            Empty string is treated as not provided.
        bot_repo: Bot repository (injected by caller via ``Injected(BotRepository)``).

    Returns:
        A non-empty engine type string. Falls back to DEFAULT_ENGINE_TYPE
        if no other source is available.
    """
    if override:
        return override

    if not bot_id:
        return DEFAULT_ENGINE_TYPE

    bot = None
    try:
        # Step 1: exact match with owner_id
        if owner_id:
            bot = bot_repo.get_by_id_and_owner(bot_id, owner_id)

        # Step 2: fallback — collaborator scenario (owner_id mismatch)
        # Skip fallback for "default" bot_id since every user has one
        if not bot and bot_id != "default":
            bot = bot_repo.get_by_id(bot_id)
    except Exception as e:
        logger.warning(
            "[resolve_engine_for_bot] lookup failed bot_id=%s owner=%s err=%s; "
            "falling back to default", bot_id, owner_id, e,
        )
        return DEFAULT_ENGINE_TYPE

    if bot:
        active = bot.get("active_engine")
        if active:
            return active

    return DEFAULT_ENGINE_TYPE
