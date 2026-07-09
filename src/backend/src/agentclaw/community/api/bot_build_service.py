"""Service API Protocol for bot build workflow."""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class BotBuildServiceProtocol(Protocol):
    """Service API for triggering bot builds."""

    def build(self, *args: Any, **kwargs: Any) -> Any: ...
