"""Service API Protocol for skill membership management."""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class SkillMemberServiceProtocol(Protocol):
    """Service API for skill membership queries and mutations."""

    def get_members_by_skill_uuid(self, *args: Any, **kwargs: Any) -> Any: ...

    def add_member(self, *args: Any, **kwargs: Any) -> Any: ...

    def remove_member(self, *args: Any, **kwargs: Any) -> Any: ...

    def is_member(self, *args: Any, **kwargs: Any) -> Any: ...

    def update_member_role(self, *args: Any, **kwargs: Any) -> Any: ...
