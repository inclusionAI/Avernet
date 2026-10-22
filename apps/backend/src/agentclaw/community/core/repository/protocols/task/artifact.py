"""Repository contract for the ``task_artifact`` append-only manifest.

Graph-driven rows are written inside the ``save_graph`` transaction (spec
2026-10-20-task-artifact-domain §5.2, ``TaskCallbackModel`` inline-write
precedent). The API-driven write (manual artifact registration endpoint)
runs through :meth:`append` in its own transaction — still append-only
manifest semantics: INSERT with select-first dedupe, no UPDATE path
anywhere. Domain imports are ``TYPE_CHECKING``-only, per
``core/repository/README.md``.
"""

from __future__ import annotations

from abc import abstractmethod
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from agentclaw.community.core.task.repository.types import TaskArtifactRecord


@runtime_checkable
class TaskArtifactRepositoryProtocol(Protocol):
    """Manifest of immutable artifacts (append + read; no UPDATE path anywhere)."""

    @abstractmethod
    def append(self, record: "TaskArtifactRecord") -> "TaskArtifactRecord":
        """Insert one artifact row in this repo's own transaction.

        select-first dedupe on ``(task_id, node_id, attempt, kind,
        content_hash)`` mirrors the ``save_graph`` inline write (spec §5.3);
        a duplicate hit is idempotent and returns the already-persisted
        record. Write failures propagate (no swallowing)."""
        ...

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