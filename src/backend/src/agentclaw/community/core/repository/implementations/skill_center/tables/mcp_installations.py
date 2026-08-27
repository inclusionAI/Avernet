"""The ONLY code that writes ``ac_bot_mcp_installation``.

The MCP twin of :mod:`.skill_installations`, row for row: an Installation
row is the fact "this MCP server is active on this Bot", keyed by the Bot's
env, which the caller resolves.
"""

from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy.exc import IntegrityError

from agentclaw.community.core.models.mcp import BotMCPInstallation
from agentclaw.community.utils.avernet_tenant import get_current_avernet_tenant


def install(session, *, bot_id: str, owner_id: str, env: str, server_code: str) -> bool:
    """Ensure the row exists; return whether this call created it.

    The same SAVEPOINT-per-row contract as ``skill_installations.install``.
    """
    present = (
        _rows(session, bot_id=bot_id, owner_id=owner_id, env=env)
        .filter(BotMCPInstallation.server_code == server_code)
        .first()
    )
    if present is not None:
        return False
    try:
        with session.begin_nested():
            session.add(
                BotMCPInstallation(
                    bot_id=bot_id,
                    owner_id=owner_id,
                    server_code=server_code,
                    env=env,
                    avernet_tenant=get_current_avernet_tenant(),
                )
            )
        return True
    except IntegrityError:
        winner = (
            _rows(session, bot_id=bot_id, owner_id=owner_id, env=env)
            .filter(BotMCPInstallation.server_code == server_code)
            .with_for_update()
            .first()
        )
        if not winner:
            raise
        return False


def uninstall(
    session, *, bot_id: str, owner_id: str, env: str, server_codes: Iterable[str]
) -> int:
    """Delete the named rows; return how many existed."""
    codes = sorted({str(code) for code in server_codes})
    if not codes:
        return 0
    return (
        _rows(session, bot_id=bot_id, owner_id=owner_id, env=env)
        .filter(BotMCPInstallation.server_code.in_(codes))
        .delete(synchronize_session=False)
    )


def uninstall_all(session, *, bot_id: str, owner_id: str, env: str) -> int:
    """Delete every row of the Bot; return how many existed."""
    return _rows(session, bot_id=bot_id, owner_id=owner_id, env=env).delete(
        synchronize_session=False
    )


def installed_codes(
    session, *, bot_id: str, owner_id: str, env: str, locked: bool = False
) -> set[str]:
    """The MCP server codes the Bot has installed; ``locked`` holds the rows."""
    query = _rows(session, bot_id=bot_id, owner_id=owner_id, env=env)
    if locked:
        query = query.with_for_update()
    return {str(row.server_code) for row in query.all()}


def _rows(session, *, bot_id: str, owner_id: str, env: str):
    return session.query(BotMCPInstallation).filter(
        BotMCPInstallation.avernet_tenant == get_current_avernet_tenant(),
        BotMCPInstallation.env == env,
        BotMCPInstallation.owner_id == owner_id,
        BotMCPInstallation.bot_id == bot_id,
    )
