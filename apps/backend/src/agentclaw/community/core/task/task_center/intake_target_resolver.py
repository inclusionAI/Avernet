"""Resolve and freeze an authorized intake target before Bot recognition."""

from __future__ import annotations

import logging
import secrets
from typing import Any

from agentclaw.community.core.task.domain.errors import TaskStateError
from agentclaw.community.core.task.domain.intake import (
    IntakeTarget, IntakeTargetType, TaskIntakeContext,
)

logger = logging.getLogger("task.intake.target")


class DefaultIntakeTargetResolver:
    def __init__(self, bot_service: Any, *, staff_profile=None, search=None) -> None:
        self._bots = bot_service
        self._staff_profile = staff_profile
        self._search = search

    def _owned_active(self, owner_user_id: str) -> list[dict[str, Any]]:
        page = 1
        items: list[dict[str, Any]] = []
        while True:
            result = self._bots.list_bots_by_owner(owner_user_id, page=page, page_size=100)
            batch = list(result.get("items") or [])
            items.extend(bot for bot in batch if isinstance(bot, dict) and
                         str(bot.get("status") or "").upper() == "ACTIVE")
            if len(batch) < 100 or page >= 20:
                break
            page += 1
        return items

    async def resolve(
        self, requested: IntakeTarget | None, message: str, context: TaskIntakeContext,
    ) -> IntakeTarget:
        owner = context.owner_user_id
        if requested is not None:
            if context.caller_bot_id is not None:
                raise TaskStateError("Bot caller cannot select another intake target")
            if requested.target_type == IntakeTargetType.GROUP:
                raise TaskStateError("Group intake requires a user-authorized group resolver")
            bot = self._bots.get_bot(requested.target_id, owner)
            if str(bot.get("status") or "").upper() != "ACTIVE":
                raise TaskStateError("requested Bot is not active")
            return requested

        if context.caller_bot_id is not None:
            bot = self._bots.get_bot(context.caller_bot_id, owner)
            if str(bot.get("status") or "").upper() != "ACTIVE":
                raise TaskStateError("caller Bot is not active")
            return IntakeTarget(IntakeTargetType.BOT, context.caller_bot_id)

        bots = self._owned_active(owner)
        if not bots:
            raise TaskStateError("NO_AVAILABLE_TARGET: create or enable a Bot")
        display_name = None
        if self._staff_profile is not None:
            try:
                display_name = self._staff_profile.get_profile_by_work_no(
                    work_no=owner,
                ).nick_name
            except Exception:
                logger.warning("staff profile unavailable for intake target resolution", exc_info=True)
        if display_name:
            same_name = [bot for bot in bots if bot.get("bot_name") == display_name]
            if len(same_name) == 1:
                return IntakeTarget(IntakeTargetType.BOT, str(same_name[0]["bot_id"]))
        default = next((bot for bot in bots if bot.get("bot_id") == "default"), None)
        if default is not None:
            return IntakeTarget(IntakeTargetType.BOT, "default")
        if self._search is not None:
            try:
                result = await self._search.search_catalog(message)
                owned = {str(bot["bot_id"]) for bot in bots}
                for candidate in result.get("candidates") or []:
                    bot_id = str(candidate.get("bot_id") or candidate.get("bot_uuid") or "").partition(":")[0]
                    if bot_id in owned:
                        return IntakeTarget(IntakeTargetType.BOT, bot_id)
            except Exception:
                logger.warning("intake target capability search failed; using random available Bot", exc_info=True)
        return IntakeTarget(IntakeTargetType.BOT, str(secrets.choice(bots)["bot_id"]))
