"""Read Bot MCP overrides with canonical entity and safe legacy addresses."""

from __future__ import annotations

import json
from typing import Any

from injector import inject

from agentclaw.community.core.bot_management.errors import BotLookupAmbiguousError
from agentclaw.community.core.models.mcp import BotMCPConfig
from agentclaw.community.core.repository.implementations.bot import mcp_config_reads
from agentclaw.community.core.repository.protocols.bot import (
    BotMCPConfigRepositoryProtocol,
)
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.plugin_api.models import BotModel
from agentclaw.community.utils.avernet_tenant import get_current_avernet_tenant
from agentclaw.community.utils.env_utils import get_current_env


class BotMCPConfigRepository(BotMCPConfigRepositoryProtocol):
    @inject
    def __init__(self, db: DatabasePlugin) -> None:
        self._db = db

    def get_by_bot_and_server_code(
        self, *, bot_id: str, owner_id: str, server_code: str
    ) -> dict[str, Any] | None:
        with self._db.orm_session() as session:
            return mcp_config_reads.get(
                session,
                bot_id=bot_id,
                owner_id=owner_id,
                server_code=server_code,
                env=get_current_env(),
            )

    def list_by_bot(self, *, bot_id: str, owner_id: str) -> dict[str, dict[str, Any]]:
        with self._db.orm_session() as session:
            return mcp_config_reads.get_all(
                session, bot_id=bot_id, owner_id=owner_id, env=get_current_env()
            )

    def list_by_owner_and_server_code(
        self, *, owner_id: str, server_code: str
    ) -> dict[str, dict[str, Any]]:
        """One user's owned Bots; owner remains a person, not a resource locator."""
        env = get_current_env()
        tenant = get_current_avernet_tenant()
        with self._db.orm_session() as session:
            bots = (
                session.query(BotModel)
                .filter(
                    BotModel.avernet_tenant == tenant,
                    BotModel.env == env,
                    BotModel.owner_id == owner_id,
                    BotModel.is_delete == 0,
                )
                .all()
            )
            owned = {}
            for bot in bots:
                if bot.bot_id in owned:
                    raise BotLookupAmbiguousError(
                        f"Ambiguous owned MCP Bot: {bot.bot_id}"
                    )
                owned[str(bot.bot_id)] = str(bot.entity_id)
            result = {}
            for bot_id, entity_id in owned.items():
                config = mcp_config_reads.get(
                    session,
                    bot_id=bot_id,
                    owner_id=entity_id,
                    env=env,
                    server_code=server_code,
                )
                if config is not None:
                    result[bot_id] = config
            # Preserve genuinely offline administrative records, never another Bot's canonical row.
            legacy = (
                session.query(BotMCPConfig)
                .filter(
                    BotMCPConfig.avernet_tenant == tenant,
                    BotMCPConfig.env == env,
                    BotMCPConfig.owner_id == owner_id,
                    BotMCPConfig.server_code == server_code,
                )
                .all()
            )
            ids = {str(row.bot_id) for row in legacy}
            existing_ids = (
                {
                    str(row[0])
                    for row in session.query(BotModel.bot_id)
                    .filter(
                        BotModel.avernet_tenant == tenant,
                        BotModel.env == env,
                        BotModel.bot_id.in_(ids),
                    )
                    .all()
                }
                if ids
                else set()
            )
            for row in legacy:
                if str(row.bot_id) not in existing_ids:
                    result[str(row.bot_id)] = json.loads(row.config)
            return result
