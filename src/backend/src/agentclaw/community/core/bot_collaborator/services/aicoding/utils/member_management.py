"""Compatibility helpers for AICoding member-management capability tests/callers."""
from __future__ import annotations

from agentclaw.community.core.bot_collaborator.services.aicoding.member_management_capability import (
    AICodingMemberManagementCapability,
)
from agentclaw.community.core.bot_collaborator.services.member_management_capability import (
    MemberManagementCapabilityService,
)
from agentclaw.community.core.workspace.runtime_identity import (
    normalize_runtime_engine,
)


def is_coding_app_bot(bot: object) -> bool:
    """Return whether the bot matches AICoding application-coding semantics.

    引擎拼写按 engine/form 词汇分裂前后两半皆认（legacy ``aicoding`` 字面
    值与 ``claude_code``）。
    """
    if not isinstance(bot, dict):
        return False
    engine = normalize_runtime_engine(bot.get("active_engine"))
    return (
        engine in ("aicoding", "claude_code")
        and bot.get("template_type") == "applicationCoding"
    )


def is_member_management_enabled_bot(bot: object) -> bool:
    """Backward-compatible helper backed by the generic capability coordinator."""
    return MemberManagementCapabilityService(
        engine_capabilities=(AICodingMemberManagementCapability(),),
    ).uses_member_management_semantics(bot)
