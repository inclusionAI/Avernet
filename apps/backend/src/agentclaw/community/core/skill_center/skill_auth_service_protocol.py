"""Service API Protocol for skill authorization."""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class SkillAuthServiceProtocol(Protocol):
    """Service API for skill access authorization checks."""

    def check_skill_permission(self, *args: Any, **kwargs: Any) -> Any: ...

    def apply_skill_permission(self, *args: Any, **kwargs: Any) -> Any: ...

    def check_bot_permission(self, *args: Any, **kwargs: Any) -> Any: ...

    def apply_bot_permission(self, *args: Any, **kwargs: Any) -> Any: ...

    def check_skill_set_permission(self, *args: Any, **kwargs: Any) -> Any: ...

    def apply_skill_set_permission(self, *args: Any, **kwargs: Any) -> Any: ...
