"""Value types shared by the SkillSet persistence contract and consumer."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class CapabilityDesiredState:
    installations: set[int]
    set_active: dict[int, bool]
    memberships: dict[int, tuple[tuple[int, str | None, str | None], ...]]
    mcp_installations: set[str] = field(default_factory=set)
    #: Complete association rows, ``(server_code, name, description, icon,
    #: user_id)`` — not bare codes: a compensation recreates these rows, and a
    #: lossy snapshot would trade a transient projection failure for permanent
    #: metadata corruption on memberships the mutation never touched.
    mcp_memberships: dict[
        int, tuple[tuple[str, str, str | None, str | None, str | None], ...]
    ] = field(default_factory=dict)
    # The Bot's Default-Set exclusion rows, ``(set_id, member)``-keyed. Part
    # of the snapshot so a compensation can restore what the exclusion
    # commands wrote — the flush treats these rows as authoritative, so a
    # restore that missed them would be silently re-applied by the next read.
    skill_exclusions: frozenset[tuple[int, int]] = frozenset()
    mcp_exclusions: frozenset[tuple[int, str]] = frozenset()


@dataclass(frozen=True)
class DesiredStateMutation:
    item: dict
    changed: bool
    previous_state: CapabilityDesiredState
    details: dict = field(default_factory=dict)
    mcp_codes: frozenset[str] = frozenset()
    """MCP codes this mutation claimed or released, if it touched any.

    Two kinds of command fill it, for the same reason: neither can name its
    MCP scope before the mutation runs. Activation learns the Set's member
    codes; a Skill mutation learns the Skill's ``mcp_dependencies``, which
    join or leave the projected MCP set along with the Skill. Both are read
    under the row lock the transaction already holds, so the scope names what
    was actually installed rather than what a second, unlocked query saw.

    Candidates, not a verdict — the projector intersects them with the set it
    resolved, so a claim that does not survive projection is never delivered
    and a release still supplied by something else is never deleted.

    Deliberately not part of ``details``: the flow spreads ``details`` into
    the command's return value and thence the HTTP response body, so putting
    runtime-projection facts there would leak them into the public API. This
    field is read by the command to build its ``ProjectionScope`` and goes no
    further.
    """


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
