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


@dataclass(frozen=True)
class BotSkillSetBridge:
    """The Skills one Bot reaches through its SkillSets, split by desired state.

    ``members`` is what the Bot's Skill listing unions with the rows it owns
    outright; Default-Set exclusions are already removed, and are the only
    thing removed.

    ``activate`` and ``deactivate`` are the Installation repair that membership
    implies — a member of an active Set must hold a row, a member of an
    inactive one must not. Both are subsets of ``members``, and an active claim
    always wins.
    """

    members: frozenset[int]
    activate: frozenset[int]
    deactivate: frozenset[int]
