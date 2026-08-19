"""Service API for type-resolved Bot Skill content and parameters."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class BotSkillAssetServiceProtocol(Protocol):
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
