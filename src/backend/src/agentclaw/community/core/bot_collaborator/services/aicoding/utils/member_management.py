"""Compatibility helpers for AICoding member-management capability tests/callers."""
from __future__ import annotations

from agentclaw.community.core.bot_collaborator.services.aicoding.member_management_capability import (
    AICodingMemberManagementCapability,
)
from agentclaw.community.core.bot_collaborator.services.member_management_capability import (
    MemberManagementCapabilityService,
    get_template_ext,
    has_template_member_management_enabled,
)


def is_coding_app_bot(bot: object) -> bool:
    """Return whether the bot matches AICoding application-coding semantics."""
    if not isinstance(bot, dict):
        return False
    return AICodingMemberManagementCapability().is_member_management_enabled(bot, None)


def has_member_management_enabled(template_ext: object) -> bool:
    """Backward-compatible alias for the generic template-ext switch."""
    return has_template_member_management_enabled(template_ext)


def is_member_management_enabled_bot(bot: object) -> bool:
    """Backward-compatible helper backed by the generic capability coordinator."""
    return MemberManagementCapabilityService(
        engine_capabilities=(AICodingMemberManagementCapability(),),
    ).uses_member_management_semantics(bot)
