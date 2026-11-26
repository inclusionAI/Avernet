"""Service API Protocol for the user-level Agent Coding work-items snapshot.

The concrete service fans out across a user's owner and collaborator Coding
Bots; this Protocol is the transport-independent contract HTTP adapters consume.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from agentclaw.community.core.aicoding.work_items_models import WorkItemsData


@runtime_checkable
class AICodingWorkItemsServiceProtocol(Protocol):
    """Service API for one user-level work-items snapshot."""

    async def get_work_items(
        self,
        *,
        current_user_id: str,
        source_filter: list[str] | None,
        since_ms: int | None,
        run_status_filter: list[str] | None,
        scan_limit: int | None,
    ) -> WorkItemsData: ...


__all__ = ["AICodingWorkItemsServiceProtocol"]
