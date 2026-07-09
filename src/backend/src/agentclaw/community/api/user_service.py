"""Service API Protocol for user (access-policy) CRUD."""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from agentclaw.community.core.access.repository import UserInfoRecord


@runtime_checkable
class UserServiceProtocol(Protocol):
    """Service API for user record management."""

    def list_users(self, *, user_type: str | None = None) -> list[UserInfoRecord]: ...

    def get_user(self, *, user_id: str, user_type: str) -> UserInfoRecord: ...

    def upsert_user(self, *, user_id: str, user_type: str, status: str) -> None: ...
