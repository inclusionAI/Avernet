"""AICoding member-management capability implementation."""
from __future__ import annotations

from typing import Any, Mapping, Optional


class AICodingMemberManagementCapability:
    """AICoding-specific member-management rules."""

    def is_member_management_enabled(
        self,
        bot: Mapping[str, Any],
        template_ext: Optional[Mapping[str, Any]],
    ) -> bool:
        """Application Coding bots use member-management semantics."""
        return (
            bot.get("active_engine") == "claude_code"
            and bot.get("template_type") == "applicationCoding"
        )
