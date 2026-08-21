"""Service API for canonical Bot-scoped SkillSet commands."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class SkillSetControlPlaneServiceProtocol(Protocol):
    """Operate one exact Bot identified by its durable owner and identifier."""

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

    async def add_skill(
        self,
        *,
        bot_id: str,
        owner_id: str,
        user_id: str,
        set_id: str,
        skill_id: str,
    ) -> dict[str, Any]: ...

    async def remove_skill(
        self,
        *,
        bot_id: str,
        owner_id: str,
        user_id: str,
        set_id: str,
        skill_id: str,
    ) -> dict[str, Any]: ...

    def list_mcps(
        self, *, bot_id: str, owner_id: str, user_id: str, set_id: str
    ) -> list[dict[str, Any]]: ...

    def mcp_permissions(
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

    async def activate_mcp_direct(
        self, *, bot_id: str, owner_id: str, user_id: str, server_code: str
    ) -> dict[str, Any]: ...

    async def deactivate_mcp_direct(
        self, *, bot_id: str, owner_id: str, user_id: str, server_code: str
    ) -> dict[str, Any]: ...

    def list_installed_mcps(
        self, *, bot_id: str, owner_id: str, user_id: str
    ) -> set[str]: ...

    async def activate(
        self, *, bot_id: str, owner_id: str, user_id: str, set_id: str
    ) -> dict[str, Any]: ...

    async def deactivate(
        self, *, bot_id: str, owner_id: str, user_id: str, set_id: str
    ) -> dict[str, Any]: ...

    def resources(
        self, *, bot_id: str, owner_id: str, user_id: str
    ) -> list[dict[str, Any]]: ...
