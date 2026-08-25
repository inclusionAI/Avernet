"""The ONLY code that reads or writes the Default-Set exclusion tables.

``ac_default_skillset_skill_exclusion`` / ``ac_default_skillset_mcp_exclusion``:
an exclusion row is the Default Set's per-Bot deactivation of one member —
the member stays the Set's, but must not hold an Installation row. The rows
are keyed by owner and Bot, not env: a Default Set is shared, its exclusions
are per-Bot.

The UoW exclusion commands (exclude/unexclude, spec E.11) land here; today
the legacy ``SkillRepository`` writers still exist and retire with them.
"""

from __future__ import annotations

from agentclaw.community.core.skill_center.orm import (
    DefaultSkillsetMcpExclusion,
    DefaultSkillsetSkillExclusion,
)
from agentclaw.community.utils.avernet_tenant import get_current_avernet_tenant


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
