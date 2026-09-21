"""Bot-scoped MCP override rows written inside capability UoW sessions."""

from __future__ import annotations

import json
from typing import Any, Iterable

from agentclaw.community.core.models.mcp import BotMCPConfig
from agentclaw.community.utils.avernet_tenant import get_current_avernet_tenant


def get_all(
    session, *, bot_id: str, owner_id: str, env: str
) -> dict[str, dict[str, Any]]:
    return {
        str(row.server_code): json.loads(row.config)
        for row in _rows(session, bot_id=bot_id, owner_id=owner_id, env=env).all()
    }


def replace(
    session,
    *,
    bot_id: str,
    owner_id: str,
    env: str,
    server_code: str,
    config: dict[str, Any] | None,
) -> bool:
    row = (
        _rows(session, bot_id=bot_id, owner_id=owner_id, env=env)
        .filter(BotMCPConfig.server_code == server_code)
        .one_or_none()
    )
    if not config:
        if row is None:
            return False
        session.delete(row)
        return True
    encoded = json.dumps(
        config, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    if row is None:
        session.add(
            BotMCPConfig(
                bot_id=bot_id,
                owner_id=owner_id,
                server_code=server_code,
                config=encoded,
                env=env,
                avernet_tenant=get_current_avernet_tenant(),
            )
        )
        return True
    if row.config == encoded:
        return False
    row.config = encoded
    return True


def delete(
    session,
    *,
    bot_id: str,
    owner_id: str,
    env: str,
    server_codes: Iterable[str],
) -> int:
    codes = sorted({str(code) for code in server_codes})
    if not codes:
        return 0
    return (
        _rows(session, bot_id=bot_id, owner_id=owner_id, env=env)
        .filter(BotMCPConfig.server_code.in_(codes))
        .delete(synchronize_session=False)
    )


def delete_all(session, *, bot_id: str, owner_id: str, env: str) -> int:
    return _rows(session, bot_id=bot_id, owner_id=owner_id, env=env).delete(
        synchronize_session=False
    )


def _rows(session, *, bot_id: str, owner_id: str, env: str):
    return session.query(BotMCPConfig).filter(
        BotMCPConfig.avernet_tenant == get_current_avernet_tenant(),
        BotMCPConfig.env == env,
        BotMCPConfig.owner_id == owner_id,
        BotMCPConfig.bot_id == bot_id,
    )
