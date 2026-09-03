"""AICoding member-management capability implementation."""
from __future__ import annotations

from typing import Any, Dict, Mapping, Optional

from agentclaw.community.core.workspace.runtime_identity import (
    normalize_runtime_engine,
)
from agentclaw.community.log import get_logger

logger = get_logger()


def has_member_management_enabled(template_ext: object) -> bool:
    """Whether AICoding template ext explicitly enables member management.

    Supported shapes include both the legacy ext form and the newer capability
    form:

        ac_templates.ext.bot_template_config.advanced_config.member_management == true
        ac_templates.ext.capabilities.member_management == true

    Only the boolean value ``True`` enables the capability; missing/malformed
    ext or truthy strings such as ``"true"`` stay disabled to avoid
    accidentally expanding collaborator management.
    """
    if not isinstance(template_ext, Mapping):
        return False

    capabilities = template_ext.get("capabilities")
    if isinstance(capabilities, Mapping):
        member_management = capabilities.get("member_management")
        if member_management is True:
            return True
        if isinstance(member_management, Mapping):
            return member_management.get("enabled") is True

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
        """AICoding coding bots or opted-in templates use member management.

        应用 Coding Bot（``applicationCoding``）与个人 Coding Bot
        （``personalCoding``）复用协作者表做应用成员管理;引擎拼写按
        engine/form 词汇分裂前后两半皆认（legacy ``aicoding`` 字面值与
        ``claude_code``）。其它 Bot 必须通过模板 ``member_management`` 显式
        开关放行；模板开关是引擎无关的显式契约，一旦开启即代表该 Bot 支持
        成员协作语义。
        """
        if self._has_template_switch_enabled(bot, bot_id):
            return True
        # 引擎拼写收敛:applicationCoding/personalCoding 的 coding bot 在
        # engine/form 词汇分裂前后两半都存在(legacy ``active_engine='aicoding'``
        # 与 ``claude_code``),按拼写任一命中即认定。枚举保持窄集——member
        # 语义不随 runtime 谓词(uses_aicoding_runtime)放宽到 architect 等
        # 其它 coding 模板形态。
        engine = normalize_runtime_engine(bot.get("active_engine"))
        return (
            engine in ("aicoding", "claude_code")
            and bot.get("template_type")
            in ("applicationCoding", "personalCoding")
        )

    def _has_template_switch_enabled(
        self,
        bot: Mapping[str, Any],
        bot_id: str | None,
    ) -> bool:
        template_config = bot.get("template_config")
        if isinstance(template_config, Mapping):
            return has_member_management_enabled(template_config)

        ext = bot.get("ext")
        if isinstance(ext, Mapping):
            nested_template_config = ext.get("template_config")
            if isinstance(nested_template_config, Mapping):
                return has_member_management_enabled(nested_template_config)

        if bot_id:
            return has_member_management_enabled(
                get_template_ext(self._template_service, bot_id)
            )
        return False
