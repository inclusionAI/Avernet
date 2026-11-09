"""Project a Bot's active Skill assets onto overview Skill summaries."""

from __future__ import annotations

from collections.abc import Iterable

from agentclaw.community.core.skills_pool.mapping_intent import (
    build_logical_skill_mappings,
)
from agentclaw.community.core.skills_pool.models import RegisteredSkillAsset

from .models import SkillSummary


def skill_summaries(assets: Iterable[RegisteredSkillAsset]) -> list[SkillSummary]:
    """Summaries in the runtime's own identity, one per delivered Skill.

    Reuses the runtime mapping intent, so the overview names exactly what the
    runtime receives: the same Skill type (``local`` / ``repo`` / ``center``),
    the same de-duplication, and the same exact Center Version. A locator the
    runtime cannot deliver raises here as it does there, so the overview never
    reports a Skill the Bot does not actually get.
    """
    summaries: list[SkillSummary] = []
    for mapping in build_logical_skill_mappings(list(assets)):
        if mapping.corpus == "center":
            summaries.append(
                SkillSummary(
                    skill_type="center",
                    skill_id=str(mapping.skill_uuid),
                    name=mapping.link_name,
                    version=mapping.sc_version_number,
                )
            )
        elif mapping.corpus in ("local", "repo"):
            summaries.append(
                SkillSummary(
                    skill_type=mapping.corpus,
                    skill_id=str(mapping.relative_path),
                    name=mapping.link_name,
                )
            )
    return summaries


__all__ = ["skill_summaries"]
