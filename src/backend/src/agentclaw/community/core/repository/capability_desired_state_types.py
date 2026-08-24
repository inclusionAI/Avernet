"""Value types shared by the SkillSet persistence contract and consumer."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class CapabilityDesiredState:
    installations: set[int]
    set_active: dict[int, bool]
    memberships: dict[int, tuple[tuple[int, str | None, str | None], ...]]
    mcp_installations: set[str] = field(default_factory=set)
    mcp_memberships: dict[int, tuple[str, ...]] = field(default_factory=dict)


@dataclass(frozen=True)
class DesiredStateMutation:
    item: dict
    changed: bool
    previous_state: CapabilityDesiredState
    details: dict = field(default_factory=dict)


@dataclass(frozen=True)
class InstallationFlushPlan:
    """What the flush resolved from Set configuration — and applied.

    ``skills_to_install`` are members of active (or Default) Sets;
    ``skills_to_uninstall`` are members only inactive claims account for.
    An excluded Default-Set member is an inactive claim — exclusion is the
    Default Set's per-Bot deactivation. R3 keeps a capability in at most one
    Set, so claims never truly compete; on historical malformed data the
    flush errs safe and keeps a row an active Set accounts for.
    ``mcps_to_install``/``mcps_to_uninstall`` are the identical split for the
    same Sets' MCP members. ``member_skill_ids`` is the reachability union
    the public listing needs — Default-Set exclusions are already removed,
    and are the only thing removed.
    """

    member_skill_ids: frozenset[int]
    skills_to_install: frozenset[int]
    skills_to_uninstall: frozenset[int]
    mcps_to_install: frozenset[str] = frozenset()
    mcps_to_uninstall: frozenset[str] = frozenset()
