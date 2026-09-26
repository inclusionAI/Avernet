"""Read-only projection helpers for the shared platform Default SkillSet."""

from __future__ import annotations

from sqlalchemy import and_, or_

from agentclaw.community.core.models.skill import SkillSet


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
