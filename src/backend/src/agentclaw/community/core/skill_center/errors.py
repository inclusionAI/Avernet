"""Domain errors raised by Skill Center operations."""


class SkillDeleteConsistencyError(RuntimeError):
    """A Skill delete could not safely converge filesystem and database state."""


class SkillReferencedBySkillSetError(RuntimeError):
    """A Skill cannot be deleted while any SkillSet still references it."""

    def __init__(self, skill_set_ids: list[str]) -> None:
        super().__init__("skill is still referenced by a skill set")
        self.skill_set_ids = skill_set_ids


class LocalSkillNotFoundError(Exception):
    """A Local Skill or its authorized Bot scope is not visible to the actor."""


class LocalSkillOwnerAmbiguousError(Exception):
    """Legacy Local Skill ownership cannot be resolved without guessing."""
