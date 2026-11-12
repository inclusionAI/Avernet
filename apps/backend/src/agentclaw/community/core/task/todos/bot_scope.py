"""Shared Bot eligibility policy for Todo creation and execution."""

from __future__ import annotations

from typing import Any

from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.spaces.models import SpaceRecord, SpaceType
from agentclaw.community.core.task.todos.errors import (
    TodoBotAmbiguousError,
    TodoBotScopeError,
)


def resolve_todo_bot(
    *,
    bot_repository: BotRepository,
    space: SpaceRecord,
    actor_id: str,
    bot_id: str,
) -> dict[str, Any]:
    """Return the only live Bot eligible for ``actor_id`` in ``space``."""

    candidates = bot_repository.list_live_by_bot_id(bot_id)
    eligible = [
        bot
        for bot in candidates
        if todo_bot_in_scope(bot=bot, space=space, actor_id=actor_id)
    ]
    if not eligible:
        raise TodoBotScopeError("Bot is not available in this Space")
    if len(eligible) > 1:
        raise TodoBotAmbiguousError("Bot id is ambiguous in this Space")
    return eligible[0]


def todo_bot_in_scope(
    *,
    bot: dict[str, Any],
    space: SpaceRecord,
    actor_id: str,
) -> bool:
    """Apply the same Team/Personal Space policy at every Todo boundary."""

    bot_space_id = bot.get("space_id")
    if space.space_type == SpaceType.TEAM:
        return bot_space_id is not None and _same_space_id(bot_space_id, space.id)
    if space.space_type != SpaceType.PERSONAL:
        return False
    if not space.personal_owner_id or space.personal_owner_id != actor_id:
        return False
    if bot.get("owner_id") != actor_id:
        return False
    if bot_space_id is not None:
        return _same_space_id(bot_space_id, space.id)
    return True


def require_todo_bot_owner_id(bot: dict[str, Any]) -> str:
    """Return the real deployment owner identity required by Context runtime."""

    owner_id = bot.get("owner_id")
    if not isinstance(owner_id, str) or not owner_id.strip():
        raise TodoBotScopeError("Bot has no usable owner identity")
    return owner_id.strip()


def todo_bot_name(bot: dict[str, Any], bot_id: str) -> str:
    value = bot.get("bot_name")
    if not isinstance(value, str) or not value.strip():
        return bot_id
    return value.strip()


def _same_space_id(value: object, space_id: int) -> bool:
    return str(value) == str(space_id)


__all__ = [
    "require_todo_bot_owner_id",
    "resolve_todo_bot",
    "todo_bot_in_scope",
    "todo_bot_name",
]
