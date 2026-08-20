"""Value types shared by the SkillSet persistence contract and consumer."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class SkillSetDesiredState:
    installations: set[int]
    set_active: dict[int, bool]
    memberships: dict[int, tuple[tuple[int, str | None, str | None], ...]]
    mcp_installations: set[str] = field(default_factory=set)
    mcp_memberships: dict[int, tuple[str, ...]] = field(default_factory=dict)


@dataclass(frozen=True)
class SkillSetMutation:
    item: dict
    changed: bool
    previous_state: SkillSetDesiredState
    details: dict = field(default_factory=dict)
