"""Member-management capability extension points for bot collaborators.

This module is engine agnostic. Engine-specific rules should implement
``EngineMemberManagementCapability`` in their own engine directories and be
wired by DI/registry code, instead of being called directly from
``CollaboratorService``.
"""
from __future__ import annotations

from typing import Any, Dict, Mapping, Optional, Protocol, Sequence

from agentclaw.community.log import get_logger

logger = get_logger()


class EngineMemberManagementCapability(Protocol):
    """Engine-specific member-management capability hook."""

    def is_member_management_enabled(
        self,
        bot: Mapping[str, Any],
        template_ext: Optional[Mapping[str, Any]],
    ) -> bool:
        """Return whether this engine enables member-management semantics."""


def has_template_member_management_enabled(template_ext: object) -> bool:
    """Whether ``ac_templates.ext`` explicitly enables member management.

    Expected shape on the template record::

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


class MemberManagementCapabilityService:
    """Engine-agnostic coordinator for collaborator/member-management support."""

    def __init__(
        self,
        template_service: Any = None,
        engine_capabilities: Sequence[EngineMemberManagementCapability] | None = None,
    ) -> None:
        self._template_service = template_service
        self._engine_capabilities = tuple(engine_capabilities or ())

    def can_manage_collaborators(self, bot: object, bot_id: str) -> bool:
        """Return whether collaborator/member management is allowed for ``bot``.

        Service bots keep the original collaborator behavior. Non-service bots
        can opt in through template ext or an engine-specific capability hook.
        """
        if not isinstance(bot, Mapping):
            return False
        if bot.get("bot_type") == "service":
            return True
        return self.uses_member_management_semantics(bot, bot_id)

    def uses_member_management_semantics(self, bot: object, bot_id: str | None = None) -> bool:
        """Return whether ``bot`` should use member-management semantics.

        This excludes the original service-bot collaborator behavior and is used
        by lock/interceptor paths that need to distinguish application/member
        management from bot-level service collaboration.
        """
        if not isinstance(bot, Mapping):
            return False
        template_ext = self._resolve_template_ext(bot, bot_id)
        if has_template_member_management_enabled(template_ext):
            return True
        for capability in self._engine_capabilities:
            try:
                if capability.is_member_management_enabled(bot, template_ext):
                    return True
            except Exception as e:  # noqa: BLE001 - fail closed per capability
                logger.warning(
                    "[MemberManagementCapabilityService] capability %s failed: %s",
                    capability.__class__.__name__,
                    e,
                )
        return False

    def _resolve_template_ext(
        self,
        bot: Mapping[str, Any],
        bot_id: str | None,
    ) -> Optional[Mapping[str, Any]]:
        template_config = bot.get("template_config")
        if isinstance(template_config, Mapping):
            return template_config
        if bot_id:
            return get_template_ext(self._template_service, bot_id)
        return None
