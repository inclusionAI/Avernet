"""Value types shared by the SkillSet persistence contract and consumer."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SkillSetDesiredState:
    installations: set[int]
    set_active: dict[int, bool]
    memberships: dict[int, tuple[tuple[int, str | None, str | None], ...]]


@dataclass(frozen=True)
class SkillSetMutation:
    item: dict
    changed: bool
    previous_state: SkillSetDesiredState
