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
    from agentclaw.community.core.mcp.scoped_config_contract import BotMCPConfigPatch


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
    """Read Bot overrides by stable entity_id in the legacy owner_id parameter.

    Canonical rows, including an empty object, win in full. A missing canonical
    row may read a uniquely attributable current-owner legacy row without writes.
    The account-level list method separately selects the human user's owned Bots.
    """

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


@runtime_checkable
class ScopedMCPConfigRepositoryProtocol(Protocol):
    """One transaction replacing user defaults and owned Bot rules."""

    @abstractmethod
    def replace(
        self,
        *,
        user_id: str,
        server_code: str,
        headers: dict[str, str],
        bot_headers: dict[str, dict[str, str]],
        owned_bot_ids: set[str],
        endpoint_env: str,
        transport_protocol: str | None,
        user_url: str | None = None,
        bot_urls: dict[str, str] | None = None,
        create_projection: bool | None = None,
        projection_consumers: frozenset[str] = frozenset(),
    ) -> dict[str, str]:
        """Replace rules and return Bot IDs and entities whose projection is tracked.

        Projection policy is unset for persistence-only callers, false to
        track existing obligations, and true to enroll known consumers.
        """
        ...


    @abstractmethod
    def patch_bot(
        self, *, bot_pk: int, bot_id: str, entity_id: str, current_owner_id: str,
        server_code: str, patch: BotMCPConfigPatch, create_projection: bool | None = None,
    ) -> tuple[dict[str, Any], bool]:
        """Merge one Bot override under the Bot mutation lock."""
        ...
