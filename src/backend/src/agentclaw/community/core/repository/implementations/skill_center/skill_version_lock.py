"""Canonical Skill -> SkillVersion row-lock order for lifecycle writes."""

from __future__ import annotations

from typing import Any

from agentclaw.community.core.models.skill import Skill
from agentclaw.community.core.models.space_skill import SkillVersion


def lock_skill_then_exact_version(
    session: Any,
    *,
    env: str,
    skill_id: int,
    skill_version_id: int,
) -> tuple[Skill, SkillVersion] | None:
    """Lock the parent Skill before one exact Version in every database."""
    skill = (
        session.query(Skill)
        .filter(Skill.id == skill_id, Skill.env == env)
        .with_for_update()
        .one_or_none()
    )
    if skill is None:
        return None
    version = (
        session.query(SkillVersion)
        .filter(
            SkillVersion.id == skill_version_id,
            SkillVersion.skill_id == skill_id,
            SkillVersion.env == env,
        )
        .with_for_update()
        .one_or_none()
    )
    if version is None:
        return None
    return skill, version


def lock_skill_then_latest_published_version(
    session: Any,
    *,
    env: str,
    skill_id: int,
) -> tuple[Skill, SkillVersion | None] | None:
    """Lock the parent Skill before its latest PUBLISHED Version."""
    skill = (
        session.query(Skill)
        .filter(Skill.id == skill_id, Skill.env == env)
        .with_for_update()
        .one_or_none()
    )
    if skill is None:
        return None
    latest = (
        session.query(SkillVersion)
        .filter(
            SkillVersion.skill_id == skill_id,
            SkillVersion.env == env,
            SkillVersion.status == "PUBLISHED",
        )
        .order_by(SkillVersion.version_ordinal.desc())
        .with_for_update()
        .first()
    )
    return skill, latest


__all__ = [
    "lock_skill_then_exact_version",
    "lock_skill_then_latest_published_version",
]
