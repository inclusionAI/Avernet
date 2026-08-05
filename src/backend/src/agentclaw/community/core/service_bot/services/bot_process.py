"""Bot-type-specific data enrichment for service-bot binding responses."""
from __future__ import annotations

from typing import Protocol

from agentclaw.community.core.bot_management.services.template_service import (
    TemplateService,
)


class BotProcess(Protocol):
    """Provide bot-type-specific fields for binding responses."""

    def get_active_runtime_engine_type(self, bot_id: str) -> str:
        """Return the explicit runtime engine type, or an empty string."""
        ...


class PersonalBotProcess:
    """Read the explicit runtime engine type from a personal bot template."""

    def __init__(self, template_service: TemplateService) -> None:
        self._template_service = template_service

    def get_active_runtime_engine_type(self, bot_id: str) -> str:
        template_config = self._template_service.get_template_config(bot_id)
        if not isinstance(template_config, dict):
            return ""

        runtime_engine_type = template_config.get("active_runtime_engine_type")
        if not isinstance(runtime_engine_type, str):
            return ""
        return runtime_engine_type.strip()


class EmptyBotProcess:
    """Default process for bot types without runtime-engine enrichment."""

    def get_active_runtime_engine_type(self, bot_id: str) -> str:
        return ""


class BotProcessRegistry:
    """Select the process implementation for a bot type."""

    def __init__(
        self,
        personal_bot_process: BotProcess,
        default_bot_process: BotProcess,
    ) -> None:
        self._personal_bot_process = personal_bot_process
        self._default_bot_process = default_bot_process

    def get(self, bot_type: str) -> BotProcess:
        if bot_type == "personal":
            return self._personal_bot_process
        return self._default_bot_process
