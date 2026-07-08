"""MCP module business-level Protocol definitions.

These protocols define the interface contract that any mcp service
dependencies must satisfy (local, prod, or test mocks).
"""
from __future__ import annotations

from typing import Any, List, Optional, Protocol, runtime_checkable


@runtime_checkable
class BotMCPProvider(Protocol):
    """Interface for fetching a bot's MCP list from skill_center.

    Implemented by ``SkillSetService``.  MCPSyncService depends only on
    this protocol so that the mcp module does not need to import
    skill_center internals.
    """

    def collect_bot_active_mcps(
        self,
        entity_id: str,
        bot_id: str,
        user_id: str,
        entity_type: str = "staff",
        engine_type: Optional[str] = None,
    ) -> List[dict[str, Any]]:
        """Collect active MCPs from the bot's active skill sets.

        Args:
            engine_type: Engine type for scoping (e.g., 'openclaw', 'aicoding').
        """
        ...

    def collect_bot_mcps(
        self,
        entity_id: str,
        bot_id: str,
        user_id: str,
        entity_type: str = "staff",
        engine_type: Optional[str] = None,
    ) -> List[dict[str, Any]]:
        """Collect all MCPs from the bot's skill sets (active and inactive)."""
        ...

    def get_bot_mcp_codes(
        self,
        entity_id: str,
        bot_id: str,
        user_id: str,
        entity_type: str = "staff",
        engine_type: Optional[str] = None,
    ) -> List[str]:
        """Return just the server_code list for the bot's active MCPs."""
        ...

    def get_active_skill_sets_mcp_summary(
        self,
        entity_id: str,
        bot_id: str,
        user_id: str,
        entity_type: str = "staff",
        engine_type: Optional[str] = None,
    ) -> tuple[list[dict[str, Any]], list[str]]:
        """Return active skill sets info plus duplicate server codes.

        Args:
            engine_type: Engine type for scoping.

        Returns:
            Tuple of (active_skill_sets_info, duplicate_server_codes).
            active_skill_sets_info: [{"id": str, "name": str, "mcp_count": int}, ...]
        """
        ...


@runtime_checkable
class UserMCPConfigRepository(Protocol):
    """Repository interface for user-level MCP configuration."""

    def get_by_user_and_server_code(
        self, user_id: str, server_code: str
    ) -> Optional[dict[str, Any]]:
        ...

    def get_by_id(self, config_id: str) -> Optional[dict[str, Any]]:
        ...

    def list_by_user(self, user_id: str) -> List[dict[str, Any]]:
        ...

    def create(self, config_data: dict[str, Any]) -> dict[str, Any]:
        ...

    def update(
        self, config_id: str, config_data: dict[str, Any]
    ) -> Optional[dict[str, Any]]:
        ...

    def delete(self, config_id: str) -> bool:
        ...
