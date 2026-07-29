"""Local-mode NotifyBotLister — reads bot mappings from persisted bot state."""
from __future__ import annotations

from agentclaw.community.core.bot_management.repository.protocol import BotRepository
from agentclaw.community.core.notify.protocol import NotifyTarget


class LocalNotifyBotLister:

    def __init__(self, bot_repository: BotRepository) -> None:
        self._bot_repository = bot_repository

    def list_bot_mappings(self, user_id: str) -> list[NotifyTarget]:
        bots = self._bot_repository.list_active_bots_by_entity(
            entity_id=user_id,
            entity_type="staff",
            bot_type="personal",
        )
        mappings: list[NotifyTarget] = []
        for bot in bots:
            binding_id = bot.get("binding_id")
            if not binding_id:
                continue
            bot_id = str(bot.get("bot_id", ""))
            if not bot_id:
                continue
            bot_name = str(bot.get("bot_name") or bot_id)
            mappings.append(NotifyTarget(
                bot_id=bot_id, bot_name=bot_name,
                owner_id=user_id, sandbox_id=str(binding_id),
            ))
        return mappings
