"""Typed, internal SkillSet Apply commands and read-only planning facts."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class BotCapabilityScope:
    bot_id: str
    owner_id: str
    engine_type: str
    default_engine_types: tuple[str, ...]
    tenant: str
    env: str


@dataclass(frozen=True)
class ResolvedSkillSetWrite:
    name: str
    description: str | None = None
    is_active: bool = True
    skill_ids: tuple[int, ...] = ()
    mcp_codes: tuple[str, ...] = ()


@dataclass(frozen=True)
class DefaultExclusionChange:
    add_skills: frozenset[tuple[int, int]] = frozenset()
    remove_skills: frozenset[tuple[int, int]] = frozenset()
    add_mcps: frozenset[tuple[int, str]] = frozenset()
    remove_mcps: frozenset[tuple[int, str]] = frozenset()


@dataclass(frozen=True)
class SkillSetSnapshot:
    id: int
    name: str
    description: str | None
    is_default: bool
    is_active: bool
    skill_ids: tuple[int, ...]
    mcp_codes: tuple[str, ...]
    skill_references: tuple[tuple[int, str | None], ...] = ()


@dataclass(frozen=True)
class SkillSetApplySnapshot:
    scope: BotCapabilityScope
    sets: tuple[SkillSetSnapshot, ...]
    skill_exclusions: frozenset[tuple[int, int]]
    mcp_exclusions: frozenset[tuple[int, str]]
    installed_skill_ids: frozenset[int]
    installed_mcp_codes: frozenset[str]
    platform_default_codes: frozenset[str]
    skills: tuple[dict[str, Any], ...] = ()
    mcp_overrides: dict[str, dict] = field(default_factory=dict)

    persistent_skill_ids: frozenset[int] = frozenset()
    local_skill_set_references: dict[int, frozenset[int]] = field(default_factory=dict)

    @property
    def raw_member_skill_ids(self) -> frozenset[int]:
        return self.persistent_skill_ids | frozenset(
            skill_id for item in self.sets for skill_id in item.skill_ids
        )


@dataclass(frozen=True)
class SkillSetApplyResult:
    created: tuple[str, ...]
    updated: tuple[str, ...]
    unchanged: tuple[str, ...]
    removed: tuple[str, ...]
    changed: bool
    runtime_projection: dict[str, Any]
    default_changed: bool
    default_removed: bool
