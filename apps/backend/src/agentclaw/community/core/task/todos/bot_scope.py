"""Shared Bot eligibility policy for Todo creation and execution."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from agentclaw.community.core.bot_public.catalog_metadata import (
    BotCatalogCaller,
    BotCatalogMetadataServiceProtocol,
    BotCatalogMetadataUnavailableError,
    BotCatalogSearchFilters,
)
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.spaces.models import SpaceRecord, SpaceType
from agentclaw.community.core.task.todos.errors import (
    TodoBotAmbiguousError,
    TodoBotScopeError,
)
from agentclaw.community.utils.avernet_tenant import get_current_avernet_tenant


def resolve_todo_bot(
    *,
    bot_repository: BotRepository,
    catalog_metadata: BotCatalogMetadataServiceProtocol,
    space: SpaceRecord,
    actor_id: str,
    bot_id: str,
) -> dict[str, Any]:
    """Return the only public, live Bot eligible in the selected Space."""

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
    bot = eligible[0]
    owner_id = require_todo_bot_owner_id(bot)
    _require_public_collaboration_bot(
        catalog_metadata=catalog_metadata,
        bot_id=bot_id,
        owner_id=owner_id,
        actor_id=actor_id,
    )
    return bot


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


def _require_public_collaboration_bot(
    *,
    catalog_metadata: BotCatalogMetadataServiceProtocol,
    bot_id: str,
    owner_id: str,
    actor_id: str,
) -> None:
    """Fail closed unless BCS confirms agent visibility is exactly public."""

    bot_uuid = f"{bot_id}:{owner_id}"
    try:
        page = catalog_metadata.search_public_bot_metadata(
            search=None,
            page=1,
            page_size=1,
            bot_uuids=(bot_uuid,),
            filters=BotCatalogSearchFilters(
                visibility=("public",),
                viewer_actor_type="bot",
                viewer_actor_id=bot_uuid,
            ),
            caller=BotCatalogCaller(
                tenant_id=get_current_avernet_tenant(),
                user_id=actor_id,
                app_id=None,
            ),
            request_id=f"todo-bot-eligibility-{uuid4().hex}",
        )
    except BotCatalogMetadataUnavailableError as exc:
        raise TodoBotScopeError(
            "Bot collaboration visibility is unavailable"
        ) from exc

    expected = (bot_id, owner_id)
    if not any(
        (item.address.bot_id, item.address.entity_id) == expected
        and item.actor_kind == "bot"
        and item.visibility == "public"
        for item in page.items
    ):
        raise TodoBotScopeError("Bot is not public for Bot collaboration")


__all__ = [
    "require_todo_bot_owner_id",
    "resolve_todo_bot",
    "todo_bot_in_scope",
    "todo_bot_name",
]
