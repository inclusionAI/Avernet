"""Typed outcomes for one ordered SkillSet Skill-membership batch."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class SkillSetSkillOutcome:
    """One requested Skill membership mutation's desired-state result.

    The legacy BFF contract deliberately permits a batch to have both
    successful and rejected members.  Keeping the domain exception here lets
    each HTTP adapter apply its own established error wire without making the
    control plane depend on HTTP response models.
    """

    skill_id: str
    changed: bool = False
    error: Exception | None = None
    runtime_projection: dict | None = field(default=None, compare=False)

    @property
    def succeeded(self) -> bool:
        return self.error is None
