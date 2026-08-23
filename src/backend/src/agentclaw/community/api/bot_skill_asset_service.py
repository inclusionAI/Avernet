"""Service API for type-resolved Bot Skill content and parameters."""

from __future__ import annotations

from abc import abstractmethod
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class BotSkillAssetServiceProtocol(Protocol):
    @abstractmethod
    def get_skill(
        self, *, skill_id: str, bot_id: str, owner_id: str, user_id: str
    ) -> dict[str, Any]: ...

    @abstractmethod
    def resolve_legacy_skill_id(
        self,
        *,
        skill_reference: str,
        source_path: str,
        bot_id: str,
        owner_id: str,
        user_id: str,
    ) -> str: ...

    @abstractmethod
    async def set_active(
        self,
        *,
        skill_id: str,
        bot_id: str,
        owner_id: str,
        user_id: str,
        active: bool,
    ) -> dict[str, Any]: ...

    @abstractmethod
    async def get_content(
        self, *, skill_id: str, bot_id: str, owner_id: str, user_id: str
    ) -> str: ...

    @abstractmethod
    async def get_parameters(
        self, *, skill_id: str, bot_id: str, owner_id: str, user_id: str
    ) -> dict[str, Any]: ...

    @abstractmethod
    async def replace_parameters(
        self,
        *,
        skill_id: str,
        bot_id: str,
        owner_id: str,
        user_id: str,
        parameters: dict[str, Any],
    ) -> dict[str, Any]: ...
