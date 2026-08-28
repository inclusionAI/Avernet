"""Typed outcomes for one ordered SkillSet member-add batch."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SkillSetAddOutcome:
    """One requested Skill's desired-state result.

    The legacy BFF contract deliberately permits a batch to have both
    successful and rejected members.  Keeping the domain exception here lets
    each HTTP adapter apply its own established error wire without making the
    control plane depend on HTTP response models.
    """

    skill_id: str
    changed: bool = False
    error: Exception | None = None

    @property
    def succeeded(self) -> bool:
        return self.error is None
