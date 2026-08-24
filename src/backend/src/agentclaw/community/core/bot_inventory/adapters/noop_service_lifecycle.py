"""Fallback service-bot lifecycle seam used until the service line plugs in."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from agentclaw.community.core.bot_inventory.protocols import ServiceLifecyclePort
from agentclaw.community.core.bot_inventory.types import (
    BotAction,
    DisplayState,
    ServiceLifecycleCard,
)


class NoopServiceLifecyclePort(ServiceLifecyclePort):
    def display_state(self, *, bot: Mapping[str, Any]) -> DisplayState:
        return DisplayState.SERVICE_DRAFT

    def allowed_actions(self, *, bot: Mapping[str, Any]) -> Sequence[BotAction]:
        return (BotAction.VIEW,)

    def cards_for_bots(
        self, *, bots: Sequence[Mapping[str, Any]]
    ) -> Mapping[str, Sequence[ServiceLifecycleCard]]:
        return {
            str(bot.get("bot_id") or ""): (
                ServiceLifecycleCard(
                    publication_id=None,
                    version=None,
                    display_state=self.display_state(bot=bot),
                    status=str(bot.get("status") or ""),
                    actions=tuple(self.allowed_actions(bot=bot)),
                ),
            )
            for bot in bots
        }
