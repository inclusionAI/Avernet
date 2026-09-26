"""Platform-owned Default MCP policy for one Bot.

These engine/template defaults are code policy, not SkillSet membership and
not Installation provenance.  Direct commands therefore cannot control them;
the Bot's only control is the Default exclusion/un-exclusion path.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from agentclaw.community.core.mcp.services._defaults import (
    get_default_mcp_server_codes,
)
from agentclaw.community.core.skill_center.policies.capability_ownership import (
    require_direct_mcp_control_allowed,
)


class PlatformDefaultMcpPolicy:
    """Resolve the platform-owned MCP codes for an exact Bot context.

    The context provider is deliberately strict.  A lookup failure is not the
    same fact as "this Bot has no template defaults" and must propagate to
    both command and runtime-projection callers.
    """

    def __init__(
        self,
        ext_info_provider: Callable[[str], Mapping[str, Any] | None],
    ) -> None:
        self._ext_info_provider = ext_info_provider

    def server_codes_for(self, bot: Mapping[str, Any]) -> frozenset[str]:
        bot_id = str(bot["bot_id"])
        return frozenset(
            get_default_mcp_server_codes(
                str(bot.get("active_engine") or ""),
                bot.get("template_type"),
                ext_info=self._ext_info_provider(bot_id),
            )
        )

    def require_direct_control_allowed(
        self,
        *,
        bot: Mapping[str, Any],
        server_code: str,
    ) -> frozenset[str]:
        """Validate ownership and return the immutable per-Bot snapshot.

        The snapshot is passed into the persistence UoW so the transactional
        boundary rechecks the same decision without resolving mutable template
        context again inside the database transaction.
        """
        platform_default_codes = self.server_codes_for(bot)
        require_direct_mcp_control_allowed(
            server_code=server_code,
            platform_default_codes=platform_default_codes,
        )
        return platform_default_codes


__all__ = ["PlatformDefaultMcpPolicy"]
