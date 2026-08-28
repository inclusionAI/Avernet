"""Bridge the inventory template port to the bot-management TemplateService."""

from __future__ import annotations

from typing import Any

from agentclaw.community.core.bot_management.services.template_service import (
    TemplateService,
)
from agentclaw.community.core.bot_inventory.protocols import (
    BotInventoryTemplatePort,
)


class TemplateServiceInventoryTemplatePort(BotInventoryTemplatePort):
    """Read ``ac_templates.ext`` by bot ids, best-effort like list attach.

    Template lookup failures are dropped to "no snapshot" — mirroring
    ``BotService._attach_template_configs_to_bots``: template trouble must
    never break a bot listing page.
    """

    def __init__(self, template_service: TemplateService) -> None:
        self._templates = template_service

    def list_template_configs_by_bot_ids(
        self, bot_ids: list[str]
    ) -> dict[str, dict[str, Any]]:
        if not bot_ids:
            return {}
        try:
            records = self._templates.list_templates_by_bot_ids(bot_ids)
        except Exception:
            return {}
        return {
            str(record.get("bot_id")): record.get("ext")
            for record in records
            if record.get("bot_id") is not None
            and isinstance(record.get("ext"), dict)
        }
