"""Service API for direct (Set-free) capability activation on one Bot."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class DirectActivationServiceProtocol(Protocol):
    """Activate/deactivate ONE capability (skill or MCP) for a Bot, directly.

    Legal only when no Set or platform Default policy governs the capability.
    Platform Default MCPs are controlled only by Default exclusion/un-exclusion.
    Same authorization, same UoW write, same compensation as the Set service:
    one pattern, two scopes.
    """

    async def activate_skill(
        self, *, skill_id: str, bot_id: str, owner_id: str, actor_id: str
    ) -> dict[str, Any]: ...

    async def deactivate_skill(
        self, *, skill_id: str, bot_id: str, owner_id: str, actor_id: str
    ) -> dict[str, Any]: ...

    async def activate_mcp(
        self, *, server_code: str, bot_id: str, owner_id: str, actor_id: str
    ) -> dict[str, Any]: ...

    async def deactivate_mcp(
        self, *, server_code: str, bot_id: str, owner_id: str, actor_id: str
    ) -> dict[str, Any]: ...

    def list_installed_mcps(
        self, *, bot_id: str, owner_id: str, actor_id: str
    ) -> set[str]:
        """The Bot's active MCP server codes — the query twin of the commands
        above, answered by the capability state reader (which flushes first)."""
        ...
