"""Member-management capability extension points for bot collaborators.

This module is engine agnostic. Engine-specific rules should implement
``EngineMemberManagementCapability`` in their own engine directories and be
wired by DI/registry code, instead of being called directly from
``CollaboratorService``.
"""
from __future__ import annotations

from typing import Any, Mapping, Protocol, Sequence

from agentclaw.community.log import get_logger

logger = get_logger()


class EngineMemberManagementCapability(Protocol):
    """Engine-specific member-management capability hook."""

    def is_member_management_enabled(
        self,
        bot: Mapping[str, Any],
        bot_id: str | None = None,
    ) -> bool:
        """Return whether this engine enables member-management semantics."""


class MemberManagementCapabilityService:
    """Engine-agnostic coordinator for collaborator/member-management support.

    This coordinator deliberately does not interpret engine-owned bot fields or
    template schemas. It only keeps the original service-bot behavior and
    delegates non-service member-management decisions to registered engine
    capabilities.
    """

    def __init__(
        self,
        engine_capabilities: Sequence[EngineMemberManagementCapability] | None = None,
    ) -> None:
        self._engine_capabilities = tuple(engine_capabilities or ())

    def can_manage_collaborators(self, bot: object, bot_id: str) -> bool:
        """Return whether collaborator/member management is allowed for ``bot``.

        Service bots keep the original collaborator behavior. Non-service bots
        must be enabled by one of the registered engine-specific capability
        hooks.
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
        for capability in self._engine_capabilities:
            try:
                if capability.is_member_management_enabled(bot, bot_id):
                    return True
            except Exception as e:  # noqa: BLE001 - fail closed per capability
                logger.warning(
                    "[MemberManagementCapabilityService] capability %s failed: %s",
                    capability.__class__.__name__,
                    e,
                )
        return False
