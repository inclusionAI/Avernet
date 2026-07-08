"""Service API Protocol for public-bot discovery (search + recommend)."""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class BotDiscoverServiceProtocol(Protocol):
    """Service API for public bot discovery."""

    def search_by_keyword(self, *args: Any, **kwargs: Any) -> Any: ...
