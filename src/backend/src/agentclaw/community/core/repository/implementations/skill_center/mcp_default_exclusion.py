"""Uncontended read-side access to platform-Default MCP exclusions."""

from injector import inject

from agentclaw.community.core.repository.protocols.mcp_default_exclusion import (
    MCPDefaultExclusionReaderProtocol,
)
from agentclaw.community.core.skill_center.orm import DefaultSkillsetMcpExclusion
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.utils.avernet_tenant import get_current_avernet_tenant


class MCPDefaultExclusionReader(MCPDefaultExclusionReaderProtocol):
    @inject
    def __init__(self, db: DatabasePlugin) -> None:
        self._db = db

    def list_excluded_server_codes(
        self, *, bot_id: str, owner_id: str
    ) -> frozenset[str]:
        with self._db.orm_session() as session:
            return frozenset(
                str(row.server_code)
                for row in session.query(DefaultSkillsetMcpExclusion)
                .filter(
                    DefaultSkillsetMcpExclusion.avernet_tenant
                    == get_current_avernet_tenant(),
                    DefaultSkillsetMcpExclusion.user_id == owner_id,
                    DefaultSkillsetMcpExclusion.bot_id == bot_id,
                )
                .all()
            )


__all__ = ["MCPDefaultExclusionReader"]
