"""Persistence contract for canonical Bot-scoped SkillSet mutations."""

from __future__ import annotations

from abc import abstractmethod
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from agentclaw.community.core.repository.skill_set_control_plane_types import (
        SkillSetDesiredState,
        SkillSetMutation,
    )


class SkillSetControlPlaneRepositoryProtocol(Protocol):
    """Tenant/env-scoped atomic SkillSet desired-state operations."""

    @abstractmethod
    def list_sets(
        self, *, bot_id: str, engine_type: str | None = None
    ) -> list[dict]: ...
    @abstractmethod
    def create_set(
        self,
        *,
        bot_id: str,
        owner_id: str,
        name: str,
        description: str | None,
        idempotency_key: str,
        engine_type: str | None = None,
    ) -> dict: ...
    @abstractmethod
    def get_set(
        self, *, bot_id: str, set_id: str, engine_type: str | None = None
    ) -> dict: ...
    @abstractmethod
    def update_set(
        self,
        *,
        bot_id: str,
        set_id: str,
        name: str | None,
        description: str | None,
        engine_type: str | None = None,
    ) -> dict: ...
    @abstractmethod
    def delete_set(
        self, *, bot_id: str, set_id: str, engine_type: str | None = None
    ) -> None: ...
    @abstractmethod
    def list_skills(
        self, *, bot_id: str, set_id: str, engine_type: str | None = None
    ) -> list[dict]: ...
    @abstractmethod
    def add_skill(
        self, *, bot_id: str, set_id: str, skill_id: str, engine_type: str | None = None
    ) -> SkillSetMutation: ...
    @abstractmethod
    def remove_skill(
        self, *, bot_id: str, set_id: str, skill_id: str, engine_type: str | None = None
    ) -> SkillSetMutation: ...
    @abstractmethod
    def set_active(
        self, *, bot_id: str, set_id: str, active: bool, engine_type: str | None = None
    ) -> SkillSetMutation: ...
    @abstractmethod
    def restore_desired_state(
        self,
        *,
        bot_id: str,
        state: SkillSetDesiredState,
        engine_type: str | None = None,
    ) -> None: ...
