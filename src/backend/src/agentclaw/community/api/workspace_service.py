"""Service API Protocol for aicoding workspace initialization."""
from __future__ import annotations

from typing import Any, Dict, Protocol, runtime_checkable


@runtime_checkable
class WorkspaceServiceProtocol(Protocol):
    """Service API for aicoding workspace setup + path resolution."""

    def get_workspace_path(self, *args: Any, **kwargs: Any) -> Any: ...

    async def initialize_workspace(self, *args: Any, **kwargs: Any) -> Dict[str, Any]: ...
