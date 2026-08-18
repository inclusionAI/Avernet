"""AICoding-specific MCP default resolution."""
from __future__ import annotations

from typing import Any, List, Mapping, Optional

from agentclaw.community.core.bot_management.engines.aicoding.default_capabilities import (
    merge_template_preset_capabilities,
)


class AicodingMcpDefaultsResolver:
    """Resolve effective default MCPs for AICoding bots.

    AICoding templates may preset MCP capabilities at
    ``ext_info.template_config.bot_template_config.preset_capabilities.mcp``.
    These are merged by ``server_code`` onto the AICoding engine default MCP list.
    """

    def resolve(
        self,
        default_servers: List[dict],
        ext_info: Optional[Mapping[str, Any]] = None,
    ) -> List[dict]:
        return merge_template_preset_capabilities(
            default_servers,
            ext_info,
            capability_key="mcp",
            identity_key="server_code",
        )
