"""Neutral API contract for frontend user-list eligibility checks."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class UserListServiceProtocol(Protocol):
    """Read current-environment membership without exposing list entries."""

    def is_in_user_list(
        self,
        *,
        entity_id: str,
        user_list_type: str,
        env: str | None = None,
    ) -> bool: ...

    def correct_membership(
        self,
        *,
        actor_id: str,
        entity_id: str,
        user_list_type: str,
        in_whitelist: bool,
    ) -> bool: ...


__all__ = ["UserListServiceProtocol"]
