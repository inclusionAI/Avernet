"""The single Legacy Default-SkillSet → Installation compatibility rule."""

from __future__ import annotations

from typing import Any


def includes_default_skill_member(
    skill: dict[str, Any], *, installed_ids: set[int], excluded_ids: set[int]
) -> bool:
    """Preserve non-Local exclusions while moving Local activity to Installation."""
    skill_id = int(skill["id"])
    if str(skill.get("git_path") or "").startswith("local://"):
        return skill_id in installed_ids
    return skill_id not in excluded_ids
