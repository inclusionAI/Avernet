"""AICoding member-management capability helpers."""
from __future__ import annotations

from typing import Any, Dict, Optional

from agentclaw.community.log import get_logger

logger = get_logger()


def is_coding_app_bot(bot: object) -> bool:
    """Return whether the bot is an application coding bot."""
    return isinstance(bot, dict) and (
        bot.get("active_engine") == "claude_code"
        and bot.get("template_type") == "applicationCoding"
    )


def has_member_management_enabled(template_ext: object) -> bool:
    """Whether ``ac_templates.ext`` explicitly enables member management.

    Expected shape on the template record::

        ac_templates.ext.bot_template_config.advanced_config.member_management == true

    Only the boolean value ``True`` enables the capability; missing/malformed
    ext or truthy strings such as ``"true"`` stay disabled to avoid
    accidentally expanding collaborator management.
    """
    if not isinstance(template_ext, dict):
        return False
    bot_template_config = template_ext.get("bot_template_config")
    if not isinstance(bot_template_config, dict):
        return False
    advanced_config = bot_template_config.get("advanced_config")
    if not isinstance(advanced_config, dict):
        return False
    return advanced_config.get("member_management") is True


def get_template_ext(template_service: Any, bot_id: str) -> Optional[Dict[str, Any]]:
    """Read ``ac_templates.ext`` through TemplateService.

    Some call sites only have the ``ac_bots`` record and therefore do not carry
    template ext. Query failures conservatively return ``None`` so member
    management is not accidentally expanded.
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
        logger.warning("[get_template_ext] Failed to get template for bot %s: %s", bot_id, e)
    return None


def is_member_management_enabled_bot(bot: object) -> bool:
    """Return true when a bot should use application/member management semantics.

    The member-management switch lives in ``ac_templates.ext``. ``BotService.get_bot``
    exposes that field as ``bot["template_config"]``.
    """
    if not isinstance(bot, dict):
        return False
    if is_coding_app_bot(bot):
        return True
    return has_member_management_enabled(bot.get("template_config"))
