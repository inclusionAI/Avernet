"""Read-only projection helpers for the shared platform Default SkillSet."""

from __future__ import annotations

from sqlalchemy import and_, or_

from agentclaw.community.core.models.skill import SkillSet
from agentclaw.community.core.skill_center.orm import (
    DefaultSkillsetMcpExclusion,
    DefaultSkillsetSkillExclusion,
)
from agentclaw.community.utils.avernet_tenant import get_current_avernet_tenant


def global_default_scope(engine_types: tuple[str | None, ...]):
    """Return the tenant-local predicate for platform-owned Default rows."""
    filters = [
        SkillSet.is_default.is_(True),
        or_(SkillSet.bolt_id == "", SkillSet.bolt_id.is_(None)),
        or_(SkillSet.user_id == "", SkillSet.user_id.is_(None)),
    ]
    normalized = tuple(engine for engine in engine_types if engine is not None)
    if normalized:
        filters.append(SkillSet.engine_type.in_(normalized))
    return and_(*filters)


def excluded_skill_ids(session, *, bot_id: str, owner_id: str, set_id: int) -> set[int]:
    """Return the addressed Bot owner's exclusions from a shared Default."""
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
    """Return the addressed Bot owner's MCP exclusions from a shared Default."""
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
