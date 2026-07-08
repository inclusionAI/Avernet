"""Service API Protocol for skill publishing."""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class SkillPublishServiceProtocol(Protocol):
    """Service API for publishing skills to the SkillCenter."""

    def publish(self, *args: Any, **kwargs: Any) -> Any: ...

    def publish_upgrade(self, *args: Any, **kwargs: Any) -> Any: ...

    def query_status(self, *args: Any, **kwargs: Any) -> Any: ...

    def list_versions(self, *args: Any, **kwargs: Any) -> Any: ...
