"""Service API for canonical Bot-scoped SkillSet commands."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol, runtime_checkable

from agentclaw.community.core.skill_center.legacy_skill_set_compatibility import (
    LegacySkillSetScope,
)
from agentclaw.community.core.skill_center.skill_set_batch import (
    SkillSetSkillOutcome,
)


from agentclaw.community.core.skill_center.skill_set_apply import (
    BotCapabilityScope, DefaultExclusionChange, ResolvedSkillSetWrite,
    SkillSetApplyResult, SkillSetApplySnapshot,
)


@runtime_checkable
class SkillSetManagementServiceProtocol(Protocol):
    """Operate one exact Bot identified by its durable owner and identifier."""

    def read_apply_snapshot(self, *, bot_id: str, owner_id: str, actor_id: str) -> SkillSetApplySnapshot:
        """Read raw planning facts without reconciling Installation."""
        ...

    async def replace_apply_skill_sets(self, *, scope: BotCapabilityScope, actor_id: str, sets: tuple[ResolvedSkillSetWrite, ...], remove_names: tuple[str, ...], default_change: DefaultExclusionChange, platform_default_codes: frozenset[str], project: bool) -> SkillSetApplyResult:
        """Replace the planned names and exclusion keys in one relationship UoW."""
        ...

    def list_sets(
        self, *, bot_id: str, owner_id: str, user_id: str
    ) -> list[dict[str, Any]]: ...

    def create_set(
        self,
        *,
        bot_id: str,
        owner_id: str,
        user_id: str,
        name: str,
        description: str | None,
    ) -> dict[str, Any]: ...

    def get_set(
        self, *, bot_id: str, owner_id: str, user_id: str, set_id: str
    ) -> dict[str, Any]: ...

    def resolve_legacy_set_scope(
        self,
        *,
        set_id: str,
        actor_id: str,
        owner_id_hint: str | None,
    ) -> LegacySkillSetScope | None: ...

    def resolve_legacy_skill_id(
        self, *, bot_id: str, owner_id: str, actor_id: str, identifier: str
    ) -> str: ...

    def update_set(
        self,
        *,
        bot_id: str,
        owner_id: str,
        user_id: str,
        set_id: str,
        name: str | None,
        description: str | None,
    ) -> dict[str, Any]: ...

    def delete_set(
        self, *, bot_id: str, owner_id: str, user_id: str, set_id: str
    ) -> None: ...

    def list_skills(
        self, *, bot_id: str, owner_id: str, user_id: str, set_id: str
    ) -> list[dict[str, Any]]: ...

    async def add_skills(
        self,
        *,
        bot_id: str,
        owner_id: str,
        user_id: str,
        set_id: str,
        skill_ids: Sequence[str],
    ) -> list[SkillSetSkillOutcome]: ...

    async def remove_skills(
        self,
        *,
        bot_id: str,
        owner_id: str,
        user_id: str,
        set_id: str,
        skill_ids: Sequence[str],
    ) -> list[SkillSetSkillOutcome]: ...

    def list_mcps(
        self, *, bot_id: str, owner_id: str, user_id: str, set_id: str
    ) -> list[dict[str, Any]]: ...

    def list_mcp_permissions(
        self, *, bot_id: str, owner_id: str, user_id: str, set_id: str
    ) -> list[dict[str, Any]]: ...

    def request_mcp_permissions(
        self,
        *,
        bot_id: str,
        owner_id: str,
        user_id: str,
        set_id: str,
        reason: str,
    ) -> list[dict[str, Any]]: ...

    async def add_mcp(
        self,
        *,
        bot_id: str,
        owner_id: str,
        user_id: str,
        set_id: str,
        server_code: str,
    ) -> dict[str, Any]: ...

    async def remove_mcp(
        self,
        *,
        bot_id: str,
        owner_id: str,
        user_id: str,
        set_id: str,
        server_code: str,
    ) -> dict[str, Any]: ...

    async def activate(
        self, *, bot_id: str, owner_id: str, user_id: str, set_id: str
    ) -> dict[str, Any]: ...

    async def deactivate(
        self, *, bot_id: str, owner_id: str, user_id: str, set_id: str
    ) -> dict[str, Any]: ...

    async def legacy_activate(
        self, *, bot_id: str, owner_id: str, actor_id: str, set_id: str
    ) -> dict[str, Any]: ...

    def list_resources(
        self, *, bot_id: str, owner_id: str, user_id: str
    ) -> list[dict[str, Any]]: ...
