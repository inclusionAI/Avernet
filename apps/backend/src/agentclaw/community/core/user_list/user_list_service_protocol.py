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
        subject_type: str = "user",
        bot_type: str = "all",
        env: str | None = None,
    ) -> bool:
        """Correct membership after the caller has passed super-admin authorization.

        HTTP adapters must reject non-operators before invoking this write.
        """
        ...


    def matches_user(
        self, *, entity_id: str, user_list_type: str, env: str, bot_type: str,
    ) -> bool:
        """Match user or department rules for an actual service/personal Bot."""
        ...


__all__ = ["UserListServiceProtocol"]
