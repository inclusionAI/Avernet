"""Read-side repository for Bot-scoped MCP Manifest overrides."""

from __future__ import annotations

import json
from typing import Any

from injector import inject

from agentclaw.community.core.models.mcp import BotMCPConfig
from agentclaw.community.core.repository.protocols.bot import (
    BotMCPConfigRepositoryProtocol,
)
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.utils.env_utils import get_current_env


class BotMCPConfigRepository(BotMCPConfigRepositoryProtocol):
    @inject
    def __init__(self, db: DatabasePlugin) -> None:
        self._db = db

    def get_by_bot_and_server_code(
        self, *, bot_id: str, owner_id: str, server_code: str
    ) -> dict[str, Any] | None:
        with self._db.orm_session() as session:
            row = (
                session.query(BotMCPConfig)
                .filter(
                    BotMCPConfig.bot_id == bot_id,
                    BotMCPConfig.owner_id == owner_id,
                    BotMCPConfig.server_code == server_code,
                    BotMCPConfig.env == get_current_env(),
                )
                .one_or_none()
            )
            return json.loads(row.config) if row is not None else None

    def list_by_bot(
        self, *, bot_id: str, owner_id: str
    ) -> dict[str, dict[str, Any]]:
        with self._db.orm_session() as session:
            rows = (
                session.query(BotMCPConfig)
                .filter(
                    BotMCPConfig.bot_id == bot_id,
                    BotMCPConfig.owner_id == owner_id,
                    BotMCPConfig.env == get_current_env(),
                )
                .all()
            )
            return {str(row.server_code): json.loads(row.config) for row in rows}

    def list_by_owner_and_server_code(
        self, *, owner_id: str, server_code: str
    ) -> dict[str, dict[str, Any]]:
        with self._db.orm_session() as session:
            rows = (
                session.query(BotMCPConfig)
                .filter(
                    BotMCPConfig.owner_id == owner_id,
                    BotMCPConfig.server_code == server_code,
                    BotMCPConfig.env == get_current_env(),
                )
                .all()
            )
            return {str(row.bot_id): json.loads(row.config) for row in rows}
