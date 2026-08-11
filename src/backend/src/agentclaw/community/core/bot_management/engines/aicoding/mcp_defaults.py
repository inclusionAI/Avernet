"""AICoding-specific MCP default resolution."""
from __future__ import annotations

from typing import Any, List, Mapping, Optional

from agentclaw.community.core.bot_management.engines.aicoding.default_capabilities import (
    AicodingDefaultCapabilitiesExtInfo,
)


class AicodingMcpDefaultsResolver:
    """Resolve effective default MCPs for AICoding bots.

    AICoding templates may preset MCP capabilities at
    ``ext_info.aicoding.template_config.bot_template_config.preset_capabilities.mcp``. These are
    merged by ``server_code`` onto the AICoding engine default MCP list.
    """

    def resolve(
        self,
        default_servers: List[dict],
        ext_info: Optional[Mapping[str, Any]] = None,
    ) -> List[dict]:
        servers = [dict(cfg) for cfg in default_servers]
        positions = {
            cfg.get("server_code"): idx
            for idx, cfg in enumerate(servers)
            if cfg.get("server_code")
        }

        for entry in self._preset_mcp_entries(ext_info):
            code = entry["server_code"]
            existing_idx = positions.get(code)
            if existing_idx is None:
                positions[code] = len(servers)
                servers.append(dict(entry))
            else:
                servers[existing_idx] = {**servers[existing_idx], **entry}

        return servers

    def _preset_mcp_entries(
        self,
        ext_info: Optional[Mapping[str, Any]],
    ) -> List[dict]:
        """Return normalized AICoding MCP presets from ext_info."""
        template_config = AicodingDefaultCapabilitiesExtInfo.from_ext_info(
            ext_info
        ).template_config
        if template_config is None:
            return []
        bot_template_config = template_config.get("bot_template_config")
        if not isinstance(bot_template_config, Mapping):
            return []
        preset_capabilities = bot_template_config.get("preset_capabilities")
        if not isinstance(preset_capabilities, Mapping):
            return []
        raw_mcps = preset_capabilities.get("mcp")
        if not isinstance(raw_mcps, list):
            return []

        entries: List[dict] = []
        for item in raw_mcps:
            if not isinstance(item, Mapping):
                continue
            code = item.get("server_code")
            if not isinstance(code, str) or not code.strip():
                continue
            entry = dict(item)
            entry["server_code"] = code.strip()
            entries.append(entry)
        return entries
