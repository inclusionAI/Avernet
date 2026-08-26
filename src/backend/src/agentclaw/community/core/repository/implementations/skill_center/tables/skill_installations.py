"""The ONLY code that writes ``ac_bot_skill_installation``.

An Installation row is the fact "this Skill is active on this Bot" — the
single source of truth every reader projects from. ``env`` is always the
Bot's env, passed by the caller: the UoW commands run where the Bot lives,
and the flush resolves it from the Bot row.
"""

from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy.exc import IntegrityError

from agentclaw.community.core.models.skill import BotSkillInstallation
from agentclaw.community.utils.avernet_tenant import get_current_avernet_tenant


def install(session, *, bot_id: str, owner_id: str, env: str, skill_id: int) -> bool:
    """Ensure the row exists; return whether this call created it.

    Each insert gets its own SAVEPOINT so a lost race rolls back that row
    alone. A losing insert is the only recoverable ``IntegrityError``, so
    anything the re-read cannot find is re-raised rather than swallowed —
    a deleted Skill, say. The re-read locks because InnoDB would otherwise
    answer it from this transaction's pre-race snapshot.
    """
    present = (
        _rows(session, bot_id=bot_id, owner_id=owner_id, env=env)
        .filter(BotSkillInstallation.skill_id == int(skill_id))
        .first()
    )
    if present is not None:
        return False
    try:
        with session.begin_nested():
            session.add(
                BotSkillInstallation(
                    bot_id=bot_id,
                    owner_id=owner_id,
                    skill_id=int(skill_id),
                    env=env,
                    avernet_tenant=get_current_avernet_tenant(),
                )
            )
        return True
    except IntegrityError:
        winner = (
            _rows(session, bot_id=bot_id, owner_id=owner_id, env=env)
            .filter(BotSkillInstallation.skill_id == int(skill_id))
            .with_for_update()
            .first()
        )
        if not winner:
            raise
        return False


def uninstall(
    session, *, bot_id: str, owner_id: str, env: str, skill_ids: Iterable[int]
) -> int:
    """Delete the named rows; return how many existed."""
    ids = sorted({int(skill_id) for skill_id in skill_ids})
    if not ids:
        return 0
    return (
        _rows(session, bot_id=bot_id, owner_id=owner_id, env=env)
        .filter(BotSkillInstallation.skill_id.in_(ids))
        .delete(synchronize_session=False)
    )


def uninstall_all(session, *, bot_id: str, owner_id: str, env: str) -> int:
    """Delete every row of the Bot; return how many existed."""
    return _rows(session, bot_id=bot_id, owner_id=owner_id, env=env).delete(
        synchronize_session=False
    )


def installed_ids(
    session, *, bot_id: str, owner_id: str, env: str, locked: bool = False
) -> set[int]:
    """The Skill ids the Bot has installed; ``locked`` holds the rows."""
    query = _rows(session, bot_id=bot_id, owner_id=owner_id, env=env)
    if locked:
        query = query.with_for_update()
    return {int(row.skill_id) for row in query.all()}


def _rows(session, *, bot_id: str, owner_id: str, env: str):
    return session.query(BotSkillInstallation).filter(
        BotSkillInstallation.avernet_tenant == get_current_avernet_tenant(),
        BotSkillInstallation.env == env,
        BotSkillInstallation.owner_id == owner_id,
        BotSkillInstallation.bot_id == bot_id,
    )
