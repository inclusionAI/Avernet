"""Repository contract for exact user-list membership checks."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class UserListRepositoryProtocol(Protocol):
    """Read the current environment's exact membership record."""

    def exists(
        self,
        *,
        entity_id: str,
        user_list_type: str,
        env: str | None = None,
    ) -> bool: ...

    def set_membership(
        self,
        *,
        entity_id: str,
        user_list_type: str,
        in_whitelist: bool,
    ) -> None: ...


__all__ = ["UserListRepositoryProtocol"]
