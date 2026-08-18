"""Fallback service-bot lifecycle seam used until the service line plugs in."""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from agentclaw.community.core.bot_inventory.protocols import ServiceLifecyclePort
from agentclaw.community.core.bot_inventory.types import BotAction, DisplayState


class NoopServiceLifecyclePort(ServiceLifecyclePort):
    def display_state(self, *, bot: Mapping[str, Any]) -> DisplayState:
        return DisplayState.SERVICE_DRAFT

    def allowed_actions(self, *, bot: Mapping[str, Any]) -> Sequence[BotAction]:
        return (BotAction.VIEW,)
