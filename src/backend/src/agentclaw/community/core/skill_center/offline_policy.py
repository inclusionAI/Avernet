"""Single invariant for commands that create a new Skill consumption fact."""

from agentclaw.community.core.skill_center.errors import SkillOfflineError


def require_skill_online(skill) -> None:
    if skill.offline_at is not None:
        raise SkillOfflineError("SKILL_OFFLINE")


__all__ = ["require_skill_online"]
