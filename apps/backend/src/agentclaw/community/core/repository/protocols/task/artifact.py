"""Read-only repository contract for the ``task_artifact`` append-only manifest.

Rows are written ONLY inside the ``save_graph`` transaction (spec
2026-10-20-task-artifact-domain §5.2, ``TaskCallbackModel`` inline-write
precedent) — this protocol never carries a write path. Domain imports are
``TYPE_CHECKING``-only, per ``core/repository/README.md``.
"""

from __future__ import annotations

from abc import abstractmethod
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from agentclaw.community.core.task.repository.types import TaskArtifactRecord


@runtime_checkable
class TaskArtifactRepositoryProtocol(Protocol):
    """Read side of the immutable artifact manifest (no UPDATE path anywhere)."""

    @abstractmethod
    def list_by_task(
        self, task_id: str, *, limit: int = 200
    ) -> "list[TaskArtifactRecord]":
        """List the task's artifacts across all nodes, attempt DESC then
        ``gmt_create`` DESC. Empty list when the task has none."""
        ...

    @abstractmethod
    def list_by_task_node(
        self, task_id: str, node_id: str, *, limit: int = 100
    ) -> "list[TaskArtifactRecord]":
        """List one node's artifacts across all attempts, attempt DESC then
        ``gmt_create`` DESC. Empty list when the node has none."""
        ...