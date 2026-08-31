"""Service API contract for service-Bot collaborative edit locks."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class ServiceEditLockInfo:
    """Public lock projection enriched with service-draft applicability."""

    lock: Any
    holder_name: str | None
    has_collaborators: bool
    is_owner: bool
    need_lock: bool


@runtime_checkable
class ServiceEditLockServiceProtocol(Protocol):
    """Authorize and manage one service Bot's collaborative edit lock."""

    def get_lock(
        self, bot_id: str, *, actor_id: str, owner_id: str
    ) -> ServiceEditLockInfo: ...

    def acquire_lock(
        self, bot_id: str, *, actor_id: str, owner_id: str
    ) -> Any: ...

    def release_lock(
        self, bot_id: str, *, actor_id: str, owner_id: str
    ) -> bool: ...

    def steal_lock(
        self, bot_id: str, *, actor_id: str, owner_id: str
    ) -> Any: ...


__all__ = ["ServiceEditLockInfo", "ServiceEditLockServiceProtocol"]
