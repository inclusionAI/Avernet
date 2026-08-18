"""Map internal lifecycle state to public inventory display state and actions."""
from __future__ import annotations

from typing import Any, Mapping

from agentclaw.community.core.bot_inventory.policies.action_policy import actions_for
from agentclaw.community.core.bot_inventory.protocols import ServiceLifecyclePort
from agentclaw.community.core.bot_inventory.types import (
    BotAction,
    BotInventoryKind,
    DisplayState,
)


class BotLifecycleView:
    def __init__(self, service_lifecycle: ServiceLifecyclePort) -> None:
        self._service_lifecycle = service_lifecycle

    def display_state(self, *, bot: Mapping[str, Any], kind: BotInventoryKind) -> DisplayState:
        if kind is BotInventoryKind.LOCAL:
            return self._local_display_state(bot)
        if kind is BotInventoryKind.SERVICE:
            return self._service_lifecycle.display_state(bot=bot)
        return self._personal_display_state(bot)

    def allowed_actions(
        self, *, bot: Mapping[str, Any], kind: BotInventoryKind
    ) -> tuple[tuple[BotAction, ...], dict[str, str]]:
        if kind is BotInventoryKind.SERVICE:
            return (tuple(self._service_lifecycle.allowed_actions(bot=bot)), {})
        return actions_for(kind=kind, display_state=self.display_state(bot=bot, kind=kind))

    @staticmethod
    def _personal_display_state(bot: Mapping[str, Any]) -> DisplayState:
        ext = bot.get("ext") or {}
        if isinstance(ext, Mapping) and ext.get("dormant"):
            return DisplayState.DORMANT
        status = str(bot.get("status") or "").upper()
        if status in {"ACTIVE", "RUNNING", "READY"}:
            return DisplayState.RUNNING
        if status in {"FAILED", "ERROR"}:
            return DisplayState.FAILED
        if status in {"DORMANT", "SLEEPING"}:
            return DisplayState.DORMANT
        return DisplayState.PENDING

    @staticmethod
    def _local_display_state(bot: Mapping[str, Any]) -> DisplayState:
        status = str(bot.get("status") or "").upper()
        if status in {"ACTIVE", "RUNNING", "READY"}:
            return DisplayState.LOCAL_RUNNING
        if status in {"OFFLINE", "RELEASED", "RELEASING"}:
            return DisplayState.LOCAL_OFFLINE
        if status in {"FAILED", "ERROR"}:
            return DisplayState.LOCAL_FAILED
        return DisplayState.LOCAL_PENDING
