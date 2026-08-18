"""Engine type resolution helper.

Frontend should not be required to know a bot's engine type — the backend
resolves it from `bot_id`. API endpoints call this helper to turn
(bot_id, owner_id, optional override) into the effective engine type used by
workspace/resource routing.

Resolution order for resolve_engine_for_bot:
  1. Explicit override (operator/debug use); empty string treated as no override.
  2. Bot record's active_engine.
  3. DEFAULT_ENGINE_TYPE.

Runtime-only callers (provider/build/workspace path routing) must use
resolve_runtime_engine_for_bot(), which additionally applies registered engine
routing policy such as claude_code + non-normalCC => aicoding.
"""
from __future__ import annotations

from typing import Optional

from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.bot_management.engines.registry import (
    resolve_bot_engine,
)
from agentclaw.community.core.workspace.constants import DEFAULT_ENGINE_TYPE
from agentclaw.community.log import get_logger

logger = get_logger()


def _lookup_bot(
    bot_id: Optional[str],
    owner_id: Optional[str],
    *,
    bot_repo: BotRepository,
    caller: str,
) -> Optional[dict]:
    """Best-effort Bot lookup shared by data/runtime resolvers."""
    if not bot_id:
        return None

    try:
        bot = None
        # Step 1: exact match with owner_id
        if owner_id:
            bot = bot_repo.get_by_id_and_owner(bot_id, owner_id)

        # Step 2: fallback — collaborator scenario (owner_id mismatch)
        # Skip fallback for "default" bot_id since every user has one.
        if not bot and bot_id != "default":
            bot = bot_repo.get_by_id(bot_id)
        return bot
    except Exception as e:
        logger.warning(
            "[%s] lookup failed bot_id=%s owner=%s err=%s; falling back to default",
            caller,
            bot_id,
            owner_id,
            e,
        )
        return None


def resolve_engine_for_bot(
    bot_id: Optional[str],
    owner_id: Optional[str] = None,
    override: Optional[str] = None,
    *,
    bot_repo: BotRepository,
) -> str:
    """Resolve the default/data engine for a Bot.

    This returns ``bot.active_engine`` and is the default resolver for data
    ownership: SkillSet.engine_type, active SkillSet queries, MCP/AgentPass
    scope, and NAS ownership. Runtime/path/provider callers must use
    :func:`resolve_runtime_engine_for_bot` explicitly.
    """
    if override:
        return override

    bot = _lookup_bot(
        bot_id, owner_id, bot_repo=bot_repo, caller="resolve_engine_for_bot"
    )
    if bot:
        active = bot.get("active_engine")
        if active:
            return active

    return DEFAULT_ENGINE_TYPE


def resolve_runtime_engine_for_bot(
    bot_id: Optional[str],
    owner_id: Optional[str] = None,
    override: Optional[str] = None,
    *,
    bot_repo: BotRepository,
) -> str:
    """Resolve the runtime/layout engine for a Bot.

    This applies registered runtime routing policies, for example
    ``claude_code`` + non-``normalCC`` => ``aicoding``. Use this only for
    provider/build/workspace path/symlink/CLI runtime layout decisions.

    ``override`` still represents the requested/base engine, but it must not
    bypass bot-aware runtime routing.  When the Bot can be found, evaluate the
    routing policy against a copy of the Bot whose ``active_engine`` is the
    override; this preserves explicit-engine compatibility while still mapping
    ``claude_code`` non-``normalCC`` Bots to the ``aicoding`` runtime layout.
    """
    bot = _lookup_bot(
        bot_id, owner_id, bot_repo=bot_repo, caller="resolve_runtime_engine_for_bot"
    )
    if bot:
        active = bot.get("active_engine")
        if override:
            routed_bot = dict(bot)
            routed_bot["active_engine"] = override
            return resolve_bot_engine(routed_bot) or override
        return resolve_bot_engine(bot) or active or DEFAULT_ENGINE_TYPE

    if override:
        return override

    return DEFAULT_ENGINE_TYPE
