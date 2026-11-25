"""Read-only canonical and legacy Bot MCP configuration queries."""

from __future__ import annotations

import json
from typing import Any

from agentclaw.community.core.models.mcp import BotMCPConfig
from agentclaw.community.core.repository.implementations.bot.mcp_config_address import (
    current_legacy_owner,
    require_same_bot,
)
from agentclaw.community.utils.avernet_tenant import get_current_avernet_tenant


def get(
    session, *, bot_id: str, owner_id: str, env: str, server_code: str
) -> dict[str, Any] | None:
    row = (
        _rows(session, bot_id=bot_id, owner_id=owner_id, env=env)
        .filter(
            BotMCPConfig.server_code == server_code,
        )
        .one_or_none()
    )
    if row is not None:
        return json.loads(row.config)
    legacy = _legacy_rows(
        session, bot_id=bot_id, owner_id=owner_id, env=env, server_codes=(server_code,)
    )
    return json.loads(legacy[0].config) if legacy else None


def get_all(
    session, *, bot_id: str, owner_id: str, env: str
) -> dict[str, dict[str, Any]]:
    result = {
        str(row.server_code): json.loads(row.config)
        for row in _rows(session, bot_id=bot_id, owner_id=owner_id, env=env).all()
    }
    for row in _legacy_rows(
        session, bot_id=bot_id, owner_id=owner_id, env=env, exclude_codes=tuple(result)
    ):
        result[str(row.server_code)] = json.loads(row.config)
    return result


def _legacy_rows(
    session,
    *,
    bot_id: str,
    owner_id: str,
    env: str,
    server_codes: tuple[str, ...] | None = None,
    exclude_codes: tuple[str, ...] = (),
):
    legacy_owner = current_legacy_owner(
        session, bot_id=bot_id, owner_id=owner_id, env=env
    )
    if legacy_owner is None:
        return []
    query = _rows(session, bot_id=bot_id, owner_id=legacy_owner, env=env)
    if server_codes is not None:
        query = query.filter(BotMCPConfig.server_code.in_(server_codes))
    if exclude_codes:
        query = query.filter(BotMCPConfig.server_code.notin_(exclude_codes))
    rows = query.all()
    if rows:
        require_same_bot(
            session,
            bot_id=bot_id,
            owner_id=owner_id,
            legacy_owner=legacy_owner,
            env=env,
        )
    return rows


def _rows(session, *, bot_id: str, owner_id: str, env: str):
    return session.query(BotMCPConfig).filter(
        BotMCPConfig.avernet_tenant == get_current_avernet_tenant(),
        BotMCPConfig.env == env,
        BotMCPConfig.owner_id == owner_id,
        BotMCPConfig.bot_id == bot_id,
    )
