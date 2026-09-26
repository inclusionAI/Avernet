"""Service API Protocol for aicoding workflow-catalog listing."""
from __future__ import annotations

from typing import List, Optional, Protocol, runtime_checkable


@runtime_checkable
class WorkflowCatalogServiceProtocol(Protocol):
    """Service API for listing AntCode workflow definitions."""

    async def list_workflows(self, branch: Optional[str] = None) -> List[dict]: ...
