"""Runtime-equivalent effective MCP reads for MCP config preflight."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from injector import inject

from agentclaw.community.core.mcp.effective_mcp_state_reader_protocol import (
    EffectiveMCPStateReaderProtocol,
)
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.repository.protocols.mcp_default_exclusion import (
    MCPDefaultExclusionReaderProtocol,
)
from agentclaw.community.core.skill_center.capability_state_contract import (
    BotCapabilityStateReaderProtocol,
)
from agentclaw.community.core.skill_center.errors import LocalSkillNotFoundError
from agentclaw.community.core.skill_center.policies.platform_default_mcp import (
    PlatformDefaultMcpPolicy,
)
from agentclaw.community.core.skill_center.runtime_resolver import (
    RuntimeDesiredState,
    resolve_effective_mcp_server_codes,
)


class EffectiveMCPStateReader(EffectiveMCPStateReaderProtocol):
    """Compose the canonical capability reader with MCP Default policy."""

    @inject
    def __init__(
        self,
        capability_reader: BotCapabilityStateReaderProtocol,
        exclusion_reader: MCPDefaultExclusionReaderProtocol,
        bot_repo: BotRepository,
        platform_default_mcp_policy: PlatformDefaultMcpPolicy,
    ) -> None:
        self._capability_reader = capability_reader
        self._exclusion_reader = exclusion_reader
        self._bot_repo = bot_repo
        self._platform_default_mcp_policy = platform_default_mcp_policy

    def effective_mcp_server_codes(
        self,
        *,
        bot_id: str,
        owner_id: str,
        bot: Mapping[str, Any] | None = None,
    ) -> frozenset[str]:
        resolved_bot = bot or self._bot_repo.get_by_id_and_owner(bot_id, owner_id)
        if resolved_bot is None:
            raise LocalSkillNotFoundError()
        snapshot = self._capability_reader.active_capabilities(
            bot_id=bot_id, owner_id=owner_id, bot=resolved_bot
        )
        excluded_codes = self._exclusion_reader.list_excluded_server_codes(
            bot_id=bot_id, owner_id=owner_id
        )
        platform_defaults = (
            self._platform_default_mcp_policy.server_codes_for(resolved_bot)
            - excluded_codes
        )
        return frozenset(
            resolve_effective_mcp_server_codes(
                RuntimeDesiredState(
                    skills=snapshot.skills,
                    installed_mcp_server_codes=snapshot.installed_mcp_server_codes,
                    system_default_mcp_server_codes=frozenset(platform_defaults),
                )
            )
        )


__all__ = ["EffectiveMCPStateReader"]
