"""Persistence contract for canonical Bot-scoped SkillSet mutations."""

from __future__ import annotations

from abc import abstractmethod
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from agentclaw.community.core.skill_center.legacy_skill_set_compatibility import (
        LegacySkillSetScope,
    )
    from agentclaw.community.core.repository.skill_set_control_plane_types import (
        BotSkillSetBridge,
        SkillSetDesiredState,
        SkillSetMutation,
    )


class SkillSetControlPlaneRepositoryProtocol(Protocol):
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
    def repair_bot_skillset_installations(
        self,
        *,
        bot_id: str,
        owner_id: str,
        env: str,
        engine_type: str | None = None,
        default_engine_types: tuple[str, ...] | None = None,
    ) -> BotSkillSetBridge:
        """Atomically make Installation agree with SkillSet membership.

        ``default_engine_types`` scopes both halves: which platform Default the
        Bot inherits, and which Defaults are retired — the ones in this tuple
        that are not the inherited one. It is the same tuple the activation
        guards gate on, so the retirement can only reach Sets whose members
        those guards refuse direct control of.
        """
        ...
    @abstractmethod
    def resolve_legacy_set_scope(
        self, *, set_id: str
    ) -> LegacySkillSetScope | None:
        """Resolve an ordinary Set address; return ``None`` for System Default."""
        ...
    @abstractmethod
    def ensure_active_skillset_installations(
        self,
        *,
        bot_id: str,
        owner_id: str,
        engine_type: str | None = None,
    ) -> int:
        """Materialize missing active-only rows for ordinary active SkillSets."""
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
    ) -> SkillSetMutation: ...
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
    ) -> SkillSetMutation: ...
    @abstractmethod
    def add_mcp(
        self,
        *,
        bot_id: str,
        owner_id: str,
        set_id: str,
        server_code: str,
        engine_type: str | None = None,
        default_engine_types: tuple[str, ...] | None = None,
    ) -> SkillSetMutation: ...
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
    ) -> SkillSetMutation: ...
    @abstractmethod
    def activate_mcp_direct(
        self, *, bot_id: str, owner_id: str, server_code: str, engine_type: str | None = None
    ) -> SkillSetMutation: ...
    @abstractmethod
    def deactivate_mcp_direct(
        self, *, bot_id: str, owner_id: str, server_code: str, engine_type: str | None = None
    ) -> SkillSetMutation: ...
    @abstractmethod
    def list_installed_mcps(
        self, *, bot_id: str, owner_id: str, engine_type: str | None = None
    ) -> set[str]: ...
    @abstractmethod
    def set_active(
        self,
        *,
        bot_id: str,
        owner_id: str,
        set_id: str,
        active: bool,
        engine_type: str | None = None,
        default_engine_types: tuple[str, ...] | None = None,
    ) -> SkillSetMutation: ...
    @abstractmethod
    def snapshot_desired_state(
        self, *, bot_id: str, owner_id: str, engine_type: str | None = None
    ) -> SkillSetDesiredState: ...
    @abstractmethod
    def restore_desired_state(
        self,
        *,
        bot_id: str,
        owner_id: str,
        state: SkillSetDesiredState,
        engine_type: str | None = None,
    ) -> None: ...
