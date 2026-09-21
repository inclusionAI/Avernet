"""Repository contracts owned by the ``bot`` domain.

Moved here by the ``core/repository`` consolidation. Every member is
``@abstractmethod``: an implementation that omits one fails at construction
naming the missing member, instead of raising ``AttributeError`` at the call
site. Domain imports are ``TYPE_CHECKING``-only — see the module docstring in
``core/repository/README.md`` for why that direction is load-bearing.
"""
from __future__ import annotations

from abc import abstractmethod
from typing import Any, List, Optional, Protocol, TYPE_CHECKING, runtime_checkable

if TYPE_CHECKING:
    pass


@runtime_checkable
class UserMCPConfigRepository(Protocol):
    """Repository interface for user-level MCP configuration."""

    @abstractmethod
    def get_by_user_and_server_code(
        self, user_id: str, server_code: str
    ) -> Optional[dict[str, Any]]:
        ...

    @abstractmethod
    def get_by_id(self, config_id: str) -> Optional[dict[str, Any]]:
        ...

    @abstractmethod
    def list_by_user(self, user_id: str) -> List[dict[str, Any]]:
        ...

    @abstractmethod
    def create(self, config_data: dict[str, Any]) -> dict[str, Any]:
        ...

    @abstractmethod
    def update(
        self, config_id: str, config_data: dict[str, Any]
    ) -> Optional[dict[str, Any]]:
        ...

    @abstractmethod
    def delete(self, config_id: str) -> bool:
        ...


@runtime_checkable
class BotMCPConfigRepositoryProtocol(Protocol):
    """Read-side repository for Bot-scoped MCP overrides."""

    @abstractmethod
    def get_by_bot_and_server_code(
        self, *, bot_id: str, owner_id: str, server_code: str
    ) -> Optional[dict[str, Any]]:
        ...

    @abstractmethod
    def list_by_bot(
        self, *, bot_id: str, owner_id: str
    ) -> dict[str, dict[str, Any]]:
        ...

    @abstractmethod
    def list_by_owner_and_server_code(
        self, *, owner_id: str, server_code: str
    ) -> dict[str, dict[str, Any]]:
        """Return Bot overrides affected by one user-level config update."""
        ...
