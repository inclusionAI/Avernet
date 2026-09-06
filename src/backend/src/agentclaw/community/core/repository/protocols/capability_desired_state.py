"""Persistence contract for canonical Bot-scoped SkillSet mutations."""

from __future__ import annotations

from abc import abstractmethod
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from agentclaw.community.core.skill_center.legacy_skill_set_compatibility import (
        LegacySkillSetScope,
    )
    from agentclaw.community.core.repository.capability_desired_state_types import (
        InstallationFlushPlan,
        CapabilityDesiredState,
        DesiredStateMutation,
    )


class CapabilityDesiredStateRepositoryProtocol(Protocol):
    """Tenant/env-scoped atomic SkillSet desired-state operations."""

    @abstractmethod
    def list_sets(
        self,
        *,
        bot_id: str,
        owner_id: str,
        engine_type: str | None = None,
        default_engine_types: tuple[str, ...] | None = None,
    ) -> list[dict]: ...
    @abstractmethod
    def flush_installations(
        self,
        *,
        bot_id: str,
        owner_id: str,
        env: str,
        engine_type: str | None = None,
        default_engine_types: tuple[str, ...] | None = None,
    ) -> InstallationFlushPlan:
        """Atomically make Installation agree with SkillSet membership.

        ``default_engine_types`` scopes both halves: which platform Default the
        Bot inherits, and which Defaults are retired — the ones in this tuple
        that are not the inherited one. It is the same tuple the activation
        guards gate on, so the retirement can only reach Sets whose members
        those guards refuse direct control of.
        """
        ...
    @abstractmethod
    def sync_default_installations(
        self,
        *,
        bot_id: str,
        owner_id: str,
        env: str,
        engine_type: str | None = None,
        default_engine_types: tuple[str, ...] | None = None,
    ) -> InstallationFlushPlan:
        """Materialize only the applicable Default Set and its exclusions.

        This is the post-backfill reader migration path.  It intentionally
        does not repair ordinary SkillSet history or infer provenance for a
        removed Default membership.
        """
        ...
    @abstractmethod
    def initialize_installations(
        self,
        *,
        bot_id: str,
        owner_id: str,
        env: str,
        engine_type: str | None = None,
        default_engine_types: tuple[str, ...] | None = None,
    ) -> InstallationFlushPlan:
        """Insert missing active Set facts without deleting existing rows."""
        ...
    @abstractmethod
    def list_member_skill_ids(
        self,
        *,
        bot_id: str,
        owner_id: str,
        engine_type: str | None = None,
        default_engine_types: tuple[str, ...] | None = None,
    ) -> frozenset[int]:
        """Read the Bot's Set reachability without materializing Installation."""
        ...
    @abstractmethod
    def resolve_legacy_set_scope(
        self, *, set_id: str
    ) -> LegacySkillSetScope | None:
        """Resolve an ordinary Set address; return ``None`` for System Default."""
        ...
    @abstractmethod
    def create_set(
        self,
        *,
        bot_id: str,
        owner_id: str,
        name: str,
        description: str | None,
        engine_type: str | None = None,
    ) -> dict: ...
    @abstractmethod
    def get_set(
        self, *, bot_id: str, owner_id: str, set_id: str, engine_type: str | None = None,
        default_engine_types: tuple[str, ...] | None = None,
    ) -> dict: ...
    @abstractmethod
    def update_set(
        self,
        *,
        bot_id: str,
        owner_id: str,
        set_id: str,
        name: str | None,
        description: str | None,
        engine_type: str | None = None,
        default_engine_types: tuple[str, ...] | None = None,
    ) -> dict: ...
    @abstractmethod
    def delete_set(
        self, *, bot_id: str, owner_id: str, set_id: str, engine_type: str | None = None,
        default_engine_types: tuple[str, ...] | None = None,
    ) -> None: ...
    @abstractmethod
    def list_skills(
        self, *, bot_id: str, owner_id: str, set_id: str, engine_type: str | None = None,
        default_engine_types: tuple[str, ...] | None = None,
    ) -> list[dict]: ...
    @abstractmethod
    def list_mcps(
        self, *, bot_id: str, owner_id: str, set_id: str, engine_type: str | None = None,
        default_engine_types: tuple[str, ...] | None = None,
    ) -> list[dict]: ...
    @abstractmethod
    def resolve_legacy_skill_id(self, *, bot_id: str, identifier: str) -> str: ...
    @abstractmethod
    def add_skill(
        self,
        *,
        bot_id: str,
        owner_id: str,
        set_id: str,
        skill_id: str,
        engine_type: str | None = None,
        default_engine_types: tuple[str, ...] | None = None,
    ) -> DesiredStateMutation: ...
    @abstractmethod
    def remove_skill(
        self,
        *,
        bot_id: str,
        owner_id: str,
        set_id: str,
        skill_id: str,
        engine_type: str | None = None,
        default_engine_types: tuple[str, ...] | None = None,
    ) -> DesiredStateMutation: ...
    @abstractmethod
    def add_mcp(
        self,
        *,
        bot_id: str,
        owner_id: str,
        set_id: str,
        server_code: str,
        name: str,
        description: str | None,
        icon: str | None,
        engine_type: str | None = None,
        default_engine_types: tuple[str, ...] | None = None,
    ) -> DesiredStateMutation: ...
    @abstractmethod
    def remove_mcp(
        self,
        *,
        bot_id: str,
        owner_id: str,
        set_id: str,
        server_code: str,
        engine_type: str | None = None,
        default_engine_types: tuple[str, ...] | None = None,
    ) -> DesiredStateMutation: ...
    @abstractmethod
    def exclude_default_skill(
        self, *, bot_id: str, owner_id: str, set_id: str, skill_id: str,
        engine_type: str | None = None,
        default_engine_types: tuple[str, ...] | None = None,
    ) -> DesiredStateMutation:
        """Default-Set per-Bot opt-out (spec E.11): exclusion row +
        Installation delta in one transaction; ``changed=False`` when the
        member is already excluded."""
        ...
    @abstractmethod
    def unexclude_default_skill(
        self, *, bot_id: str, owner_id: str, set_id: str, skill_id: str,
        engine_type: str | None = None,
        default_engine_types: tuple[str, ...] | None = None,
    ) -> DesiredStateMutation: ...
    @abstractmethod
    def exclude_default_mcp(
        self, *, bot_id: str, owner_id: str, set_id: str, server_code: str,
        engine_type: str | None = None,
        default_engine_types: tuple[str, ...] | None = None,
        platform_default_codes: frozenset[str] = frozenset(),
    ) -> DesiredStateMutation:
        """``platform_default_codes`` is the caller-resolved engine/template
        default policy — the unmaterialized half of Default-Set MCP
        membership. A code in neither it nor the association rows is refused
        as ``changed=False`` without writing an exclusion row."""
        ...
    @abstractmethod
    def unexclude_default_mcp(
        self, *, bot_id: str, owner_id: str, set_id: str, server_code: str,
        engine_type: str | None = None,
        default_engine_types: tuple[str, ...] | None = None,
    ) -> DesiredStateMutation: ...
    @abstractmethod
    def excluded_default_skill_ids(
        self, *, bot_id: str, owner_id: str, set_id: str
    ) -> set[int]: ...
    @abstractmethod
    def excluded_default_mcp_codes(
        self, *, bot_id: str, owner_id: str, set_id: str
    ) -> set[str]: ...
    @abstractmethod
    def install_skill(
        self, *, bot_id: str, owner_id: str, skill_id: str,
        engine_type: str | None = None,
        default_engine_types: tuple[str, ...] | None = None,
    ) -> DesiredStateMutation:
        """Write the direct Installation fact for one Skill.

        R1 is decided here, under the transaction: a Skill any of the Bot's
        Sets holds — the Default included, excluded or not — refuses direct
        control with ``RESOURCE_MANAGED_BY_SKILL_SET``.
        """
        ...
    @abstractmethod
    def uninstall_skill(
        self, *, bot_id: str, owner_id: str, skill_id: str,
        engine_type: str | None = None,
        default_engine_types: tuple[str, ...] | None = None,
    ) -> DesiredStateMutation: ...
    @abstractmethod
    def install_mcp(
        self, *, bot_id: str, owner_id: str, server_code: str,
        platform_default_codes: frozenset[str],
        engine_type: str | None = None,
        default_engine_types: tuple[str, ...] | None = None,
    ) -> DesiredStateMutation:
        """The MCP twin of :meth:`install_skill`, plus platform-policy ownership.

        ``platform_default_codes`` is resolved strictly from the Bot's
        engine/template context. A matching code refuses Direct control; only
        Default exclusion/un-exclusion may change its effective state.
        """
        ...
    @abstractmethod
    def uninstall_mcp(
        self, *, bot_id: str, owner_id: str, server_code: str,
        platform_default_codes: frozenset[str],
        engine_type: str | None = None,
        default_engine_types: tuple[str, ...] | None = None,
    ) -> DesiredStateMutation: ...
    @abstractmethod
    def list_installed_mcps(
        self, *, bot_id: str, owner_id: str, engine_type: str | None = None
    ) -> set[str]: ...
    @abstractmethod
    def set_skill_set_active(
        self,
        *,
        bot_id: str,
        owner_id: str,
        set_id: str,
        active: bool,
        engine_type: str | None = None,
        default_engine_types: tuple[str, ...] | None = None,
    ) -> DesiredStateMutation: ...
    @abstractmethod
    def snapshot_desired_state(
        self, *, bot_id: str, owner_id: str, engine_type: str | None = None
    ) -> CapabilityDesiredState: ...
    @abstractmethod
    def restore_desired_state(
        self,
        *,
        bot_id: str,
        owner_id: str,
        state: CapabilityDesiredState,
        engine_type: str | None = None,
    ) -> None: ...
