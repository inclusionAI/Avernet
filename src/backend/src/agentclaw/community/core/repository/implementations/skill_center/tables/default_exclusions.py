"""The ONLY code that reads or writes the Default-Set exclusion tables.

``ac_default_skillset_skill_exclusion`` / ``ac_default_skillset_mcp_exclusion``:
an exclusion row is the Default Set's per-Bot deactivation of one member —
the member stays the Set's, but must not hold an Installation row. The rows
are keyed by owner and Bot, not env: a Default Set is shared, its exclusions
are per-Bot.

The UoW exclusion commands (spec E.11) compose these with the Installation
deltas in one transaction; the legacy ``SkillRepository`` writers retire
with their dead callers.

These functions are deliberately dumb SQL: they do not re-verify that
``set_id`` names a Default Set or that the member belongs to it. Those
invariants are the UoW commands' — ``_default_set`` refuses a non-Default
address and the exclusion commands gate on effective membership before
writing — and the write-ownership architecture test keeps the UoW composition
modules this package's only importers, so no caller can reach these writes
without passing those gates.
"""

from __future__ import annotations

from agentclaw.community.core.skill_center.orm import (
    DefaultSkillsetMcpExclusion,
    DefaultSkillsetSkillExclusion,
)
from agentclaw.community.utils.avernet_tenant import get_current_avernet_tenant


def exclude_skill(
    session, *, bot_id: str, owner_id: str, set_id: int, skill_id: int
) -> bool:
    """Ensure the exclusion row exists; return whether this call created it."""
    if skill_id in excluded_skill_ids(
        session, bot_id=bot_id, owner_id=owner_id, set_id=set_id
    ):
        return False
    session.add(
        DefaultSkillsetSkillExclusion(
            user_id=owner_id,
            bot_id=bot_id,
            skill_set_id=int(set_id),
            skill_id=int(skill_id),
            avernet_tenant=get_current_avernet_tenant(),
        )
    )
    session.flush()
    return True


def unexclude_skill(
    session, *, bot_id: str, owner_id: str, set_id: int, skill_id: int
) -> bool:
    """Delete the exclusion row; return whether it existed."""
    return (
        session.query(DefaultSkillsetSkillExclusion)
        .filter(
            DefaultSkillsetSkillExclusion.avernet_tenant
            == get_current_avernet_tenant(),
            DefaultSkillsetSkillExclusion.user_id == owner_id,
            DefaultSkillsetSkillExclusion.bot_id == bot_id,
            DefaultSkillsetSkillExclusion.skill_set_id == int(set_id),
            DefaultSkillsetSkillExclusion.skill_id == int(skill_id),
        )
        .delete(synchronize_session=False)
        > 0
    )


def exclude_mcp(
    session, *, bot_id: str, owner_id: str, set_id: int, server_code: str
) -> bool:
    """Ensure the exclusion row exists; return whether this call created it."""
    if server_code in excluded_mcp_codes(
        session, bot_id=bot_id, owner_id=owner_id, set_id=set_id
    ):
        return False
    session.add(
        DefaultSkillsetMcpExclusion(
            user_id=owner_id,
            bot_id=bot_id,
            skill_set_id=int(set_id),
            server_code=server_code,
            avernet_tenant=get_current_avernet_tenant(),
        )
    )
    session.flush()
    return True


def unexclude_mcp(
    session, *, bot_id: str, owner_id: str, set_id: int, server_code: str
) -> bool:
    """Delete the exclusion row; return whether it existed."""
    return (
        session.query(DefaultSkillsetMcpExclusion)
        .filter(
            DefaultSkillsetMcpExclusion.avernet_tenant
            == get_current_avernet_tenant(),
            DefaultSkillsetMcpExclusion.user_id == owner_id,
            DefaultSkillsetMcpExclusion.bot_id == bot_id,
            DefaultSkillsetMcpExclusion.skill_set_id == int(set_id),
            DefaultSkillsetMcpExclusion.server_code == server_code,
        )
        .delete(synchronize_session=False)
        > 0
    )


def all_skill_exclusions(
    session, *, bot_id: str, owner_id: str
) -> set[tuple[int, int]]:
    """Every ``(set_id, skill_id)`` exclusion the owner holds for this Bot."""
    return {
        (int(row.skill_set_id), int(row.skill_id))
        for row in session.query(DefaultSkillsetSkillExclusion)
        .filter(
            DefaultSkillsetSkillExclusion.avernet_tenant
            == get_current_avernet_tenant(),
            DefaultSkillsetSkillExclusion.user_id == owner_id,
            DefaultSkillsetSkillExclusion.bot_id == bot_id,
        )
        .all()
    }


def all_mcp_exclusions(
    session, *, bot_id: str, owner_id: str
) -> set[tuple[int, str]]:
    """Every ``(set_id, server_code)`` exclusion the owner holds for this Bot."""
    return {
        (int(row.skill_set_id), str(row.server_code))
        for row in session.query(DefaultSkillsetMcpExclusion)
        .filter(
            DefaultSkillsetMcpExclusion.avernet_tenant
            == get_current_avernet_tenant(),
            DefaultSkillsetMcpExclusion.user_id == owner_id,
            DefaultSkillsetMcpExclusion.bot_id == bot_id,
        )
        .all()
    }


def replace_all(
    session,
    *,
    bot_id: str,
    owner_id: str,
    skill_exclusions: frozenset[tuple[int, int]],
    mcp_exclusions: frozenset[tuple[int, str]],
) -> None:
    """Make the Bot's exclusion rows say exactly this — the restore path."""
    session.query(DefaultSkillsetSkillExclusion).filter(
        DefaultSkillsetSkillExclusion.avernet_tenant
        == get_current_avernet_tenant(),
        DefaultSkillsetSkillExclusion.user_id == owner_id,
        DefaultSkillsetSkillExclusion.bot_id == bot_id,
    ).delete(synchronize_session=False)
    session.query(DefaultSkillsetMcpExclusion).filter(
        DefaultSkillsetMcpExclusion.avernet_tenant
        == get_current_avernet_tenant(),
        DefaultSkillsetMcpExclusion.user_id == owner_id,
        DefaultSkillsetMcpExclusion.bot_id == bot_id,
    ).delete(synchronize_session=False)
    session.flush()
    for set_id, skill_id in sorted(skill_exclusions):
        session.add(
            DefaultSkillsetSkillExclusion(
                user_id=owner_id,
                bot_id=bot_id,
                skill_set_id=int(set_id),
                skill_id=int(skill_id),
                avernet_tenant=get_current_avernet_tenant(),
            )
        )
    for set_id, server_code in sorted(mcp_exclusions):
        session.add(
            DefaultSkillsetMcpExclusion(
                user_id=owner_id,
                bot_id=bot_id,
                skill_set_id=int(set_id),
                server_code=server_code,
                avernet_tenant=get_current_avernet_tenant(),
            )
        )
    session.flush()


def excluded_skill_ids(session, *, bot_id: str, owner_id: str, set_id: int) -> set[int]:
    """The addressed Bot owner's Skill exclusions from a shared Default."""
    return {
        int(value[0])
        for value in session.query(DefaultSkillsetSkillExclusion.skill_id)
        .filter(
            DefaultSkillsetSkillExclusion.avernet_tenant
            == get_current_avernet_tenant(),
            DefaultSkillsetSkillExclusion.user_id == owner_id,
            DefaultSkillsetSkillExclusion.bot_id == bot_id,
            DefaultSkillsetSkillExclusion.skill_set_id == set_id,
        )
        .all()
    }


def excluded_mcp_codes(session, *, bot_id: str, owner_id: str, set_id: int) -> set[str]:
    """The addressed Bot owner's MCP exclusions from a shared Default."""
    return {
        str(value[0])
        for value in session.query(DefaultSkillsetMcpExclusion.server_code)
        .filter(
            DefaultSkillsetMcpExclusion.avernet_tenant
            == get_current_avernet_tenant(),
            DefaultSkillsetMcpExclusion.user_id == owner_id,
            DefaultSkillsetMcpExclusion.bot_id == bot_id,
            DefaultSkillsetMcpExclusion.skill_set_id == set_id,
        )
        .all()
    }
