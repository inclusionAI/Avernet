"""Bot override values at a canonical entity key, with safe legacy compatibility."""

from __future__ import annotations

import json
from typing import Any, Iterable

from agentclaw.community.core.mcp.config_comparison import comparable_config
from agentclaw.community.core.models.mcp import BotMCPConfig
from agentclaw.community.core.repository.implementations.bot.mcp_config_reads import (
    get_all as get_all,
    _rows,
    _legacy_rows,
)
from agentclaw.community.utils.avernet_tenant import get_current_avernet_tenant


def replace(
    session,
    *,
    bot_id: str,
    owner_id: str,
    env: str,
    server_code: str,
    config: dict[str, Any] | None,
    preserve_equivalent: bool = False,
) -> bool:
    changed, value_changed = _replace(
        session,
        bot_id=bot_id,
        owner_id=owner_id,
        env=env,
        server_code=server_code,
        config=config,
        preserve_equivalent=preserve_equivalent,
    )
    return value_changed if preserve_equivalent else changed


def replace_apply(
    session,
    *,
    bot_id: str,
    owner_id: str,
    env: str,
    server_code: str,
    config: dict[str, Any] | None,
) -> tuple[bool, bool]:
    """Return actual persistence change and effective config change separately.

    Apply consumes aliases even when the effective value is unchanged. That is
    a durable Bot mutation, but does not expand the MCP runtime update scope.
    """
    return _replace(
        session,
        bot_id=bot_id,
        owner_id=owner_id,
        env=env,
        server_code=server_code,
        config=config,
        preserve_equivalent=True,
    )


def _replace(
    session,
    *,
    bot_id: str,
    owner_id: str,
    env: str,
    server_code: str,
    config: dict[str, Any] | None,
    preserve_equivalent: bool = False,
) -> tuple[bool, bool]:
    row = (
        _rows(session, bot_id=bot_id, owner_id=owner_id, env=env)
        .filter(
            BotMCPConfig.server_code == server_code,
        )
        .one_or_none()
    )
    legacy = _legacy_rows(
        session, bot_id=bot_id, owner_id=owner_id, env=env, server_codes=(server_code,)
    )
    previous = (
        json.loads(row.config)
        if row is not None
        else json.loads(legacy[0].config)
        if legacy
        else None
    )
    value_changed = comparable_config(previous) != comparable_config(config)
    for old in legacy:
        session.delete(old)
    if not config:
        if row is not None:
            session.delete(row)
        return row is not None or bool(legacy), value_changed
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
        return True, value_changed
    if row.config == encoded or (
        preserve_equivalent
        and comparable_config(json.loads(row.config)) == comparable_config(config)
    ):
        return bool(legacy), value_changed
    row.config = encoded
    return True, value_changed


def delete(
    session, *, bot_id: str, owner_id: str, env: str, server_codes: Iterable[str]
) -> int:
    codes = tuple(sorted(set(server_codes)))
    if not codes:
        return 0
    legacy = _legacy_rows(
        session, bot_id=bot_id, owner_id=owner_id, env=env, server_codes=codes
    )
    for row in legacy:
        session.delete(row)
    return len(legacy) + _rows(
        session, bot_id=bot_id, owner_id=owner_id, env=env
    ).filter(
        BotMCPConfig.server_code.in_(codes),
    ).delete(synchronize_session=False)


def delete_all(session, *, bot_id: str, owner_id: str, env: str) -> int:
    legacy = _legacy_rows(session, bot_id=bot_id, owner_id=owner_id, env=env)
    for row in legacy:
        session.delete(row)
    return len(legacy) + _rows(
        session, bot_id=bot_id, owner_id=owner_id, env=env
    ).delete(synchronize_session=False)


def normalize(session, *, bot_id: str, owner_id: str, env: str) -> None:
    """Move safe aliases without replaying an earlier configuration snapshot."""
    for legacy in _legacy_rows(session, bot_id=bot_id, owner_id=owner_id, env=env):
        canonical = (
            _rows(session, bot_id=bot_id, owner_id=owner_id, env=env)
            .filter(
                BotMCPConfig.server_code == legacy.server_code,
            )
            .with_for_update()
            .one_or_none()
        )
        if canonical is not None:
            session.delete(legacy)
        else:
            legacy.owner_id = owner_id
