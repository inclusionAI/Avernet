"""Atomic persistence of one user's scoped MCP connection rules."""

from __future__ import annotations

import json
from typing import Any

from injector import inject

from agentclaw.community.core.models.mcp import BotMCPConfig, UserMCPConfig
from agentclaw.community.core.repository.implementations.skill_center.tables import (
    bot_mcp_configs,
)
from agentclaw.community.core.repository.protocols.bot.mcp import (
    ScopedMCPConfigRepositoryProtocol,
)
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.utils.avernet_tenant import get_current_avernet_tenant
from agentclaw.community.utils.env_utils import get_current_env


def _json_object(value: str | None) -> dict[str, Any]:
    parsed = json.loads(value) if value else {}
    if not isinstance(parsed, dict):
        raise ValueError("MCP config JSON must be an object")
    return parsed


class ScopedMCPConfigRepository(ScopedMCPConfigRepositoryProtocol):
    """Replace user and owner-qualified Bot rules in one transaction."""

    @inject
    def __init__(self, db: DatabasePlugin) -> None:
        self._db = db

    def replace(
        self,
        *,
        user_id: str,
        server_code: str,
        headers: dict[str, str],
        bot_headers: dict[str, dict[str, str]],
        owned_bot_ids: set[str],
        endpoint_env: str,
        transport_protocol: str | None,
        user_url: str | None = None,
        bot_urls: dict[str, str] | None = None,
    ) -> None:
        if not bot_headers.keys() <= owned_bot_ids:
            raise ValueError("Bot Header scope contains a Bot not owned by the user")
        if bot_urls is not None and not bot_urls.keys() <= owned_bot_ids:
            raise ValueError("Bot URL scope contains a Bot not owned by the user")
        if bot_urls is None and user_url is not None:
            raise ValueError("user_url requires an explicit URL replacement")
        env = get_current_env()
        tenant = get_current_avernet_tenant()
        with self._db.transactional_orm_session() as session:
            user_row = (
                session.query(UserMCPConfig)
                .filter(
                    UserMCPConfig.avernet_tenant == tenant,
                    UserMCPConfig.env == env,
                    UserMCPConfig.user_id == user_id,
                    UserMCPConfig.server_code == server_code,
                )
                .with_for_update()
                .one_or_none()
            )
            extra = _json_object(user_row.extra_config if user_row else None)
            extra.update(
                headers=headers,
                endpoint_env=endpoint_env,
                transport_protocol=transport_protocol,
            )
            if bot_urls is not None:
                if user_url is None:
                    extra.pop("url", None)
                else:
                    extra["url"] = user_url
            encoded_user = json.dumps(extra, ensure_ascii=False, sort_keys=True)
            if user_row is None:
                session.add(
                    UserMCPConfig(
                        user_id=user_id,
                        server_code=server_code,
                        api_key=extra.get("api_key"),
                        extra_config=encoded_user,
                        env=env,
                        avernet_tenant=tenant,
                    )
                )
            else:
                user_row.extra_config = encoded_user

            rows = (
                session.query(BotMCPConfig)
                .filter(
                    BotMCPConfig.avernet_tenant == tenant,
                    BotMCPConfig.env == env,
                    BotMCPConfig.owner_id == user_id,
                    BotMCPConfig.server_code == server_code,
                    BotMCPConfig.bot_id.in_(owned_bot_ids),
                )
                .with_for_update()
                .all()
            )
            existing = {str(row.bot_id): _json_object(row.config) for row in rows}
            for bot_id in sorted(existing.keys() | bot_headers.keys() | (bot_urls or {}).keys()):
                config = dict(existing.get(bot_id) or {})
                if bot_headers.get(bot_id):
                    config["headers"] = bot_headers[bot_id]
                else:
                    config.pop("headers", None)
                if bot_urls is not None:
                    if bot_id in bot_urls:
                        config["url"] = bot_urls[bot_id]
                    else:
                        config.pop("url", None)
                bot_mcp_configs.replace(
                    session,
                    bot_id=bot_id,
                    owner_id=user_id,
                    server_code=server_code,
                    config=config or None,
                    env=env,
                )
