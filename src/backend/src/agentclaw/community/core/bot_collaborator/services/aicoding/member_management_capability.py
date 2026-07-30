"""AICoding member-management capability implementation."""
from __future__ import annotations

from typing import Any, Dict, Mapping, Optional

from agentclaw.community.log import get_logger

logger = get_logger()


def has_member_management_enabled(template_ext: object) -> bool:
    """Whether AICoding template ext explicitly enables member management.

    Expected shape on the AICoding template record::

        ac_templates.ext.bot_template_config.advanced_config.member_management == true

    Only the boolean value ``True`` enables the capability; missing/malformed
    ext or truthy strings such as ``"true"`` stay disabled to avoid
    accidentally expanding collaborator management.
    """
    if not isinstance(template_ext, Mapping):
        return False
    bot_template_config = template_ext.get("bot_template_config")
    if not isinstance(bot_template_config, Mapping):
        return False
    advanced_config = bot_template_config.get("advanced_config")
    if not isinstance(advanced_config, Mapping):
        return False
    return advanced_config.get("member_management") is True


def get_template_ext(template_service: Any, bot_id: str) -> Optional[Dict[str, Any]]:
    """Read AICoding template ext through TemplateService.

    This helper lives in the AICoding capability because the ext schema is an
    AICoding-owned opt-in mechanism, not a generic collaborator concept.
    Query failures conservatively return ``None`` so member management is not
    accidentally expanded.
    """
    if template_service is None:
        return None
    try:
        get_template_config = getattr(template_service, "get_template_config", None)
        if callable(get_template_config):
            config = get_template_config(bot_id)
            return config if isinstance(config, dict) else None

        get_template = getattr(template_service, "get_template", None)
        if callable(get_template):
            template = get_template(bot_id)
            if isinstance(template, dict):
                ext = template.get("ext")
                return ext if isinstance(ext, dict) else None
    except Exception as e:
        logger.warning(
            "[AICodingMemberManagementCapability] failed to get template for bot %s: %s",
            bot_id,
            e,
        )
    return None


class AICodingMemberManagementCapability:
    """AICoding-specific member-management rules."""

    def __init__(self, template_service: Any = None) -> None:
        self._template_service = template_service

    def is_member_management_enabled(
        self,
        bot: Mapping[str, Any],
        bot_id: str | None = None,
    ) -> bool:
        """AICoding app bots or opted-in AICoding templates use member management."""
        if self._has_template_switch_enabled(bot, bot_id):
            return True
        return (
            bot.get("active_engine") == "claude_code"
            and bot.get("template_type") == "applicationCoding"
        )

    def _has_template_switch_enabled(
        self,
        bot: Mapping[str, Any],
        bot_id: str | None,
    ) -> bool:
        template_config = bot.get("template_config")
        if isinstance(template_config, Mapping):
            return has_member_management_enabled(template_config)
        if bot_id:
            return has_member_management_enabled(
                get_template_ext(self._template_service, bot_id)
            )
        return False
