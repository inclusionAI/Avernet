"""Service API for type-resolved Bot Skill content and parameters."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class BotSkillAssetServiceProtocol(Protocol):
    def get_skill(self, *, skill_id: str, bot_id: str, actor_id: str) -> dict[str, Any]: ...

    def resolve_legacy_skill_id(
        self,
        *,
        skill_reference: str,
        source_path: str,
        bot_id: str,
        actor_id: str,
    ) -> str: ...

    async def set_active(
        self, *, skill_id: str, bot_id: str, actor_id: str, active: bool
    ) -> dict[str, Any]: ...

    async def get_content(
        self, *, skill_id: str, bot_id: str, actor_id: str
    ) -> str: ...

    async def get_parameters(
        self, *, skill_id: str, bot_id: str, actor_id: str
    ) -> dict[str, Any]: ...

    async def replace_parameters(
        self,
        *,
        skill_id: str,
        bot_id: str,
        actor_id: str,
        parameters: dict[str, Any],
    ) -> dict[str, Any]: ...
