"""Repository contracts for the collaboration-task persistence layer.

Every member is ``@abstractmethod`` (an implementation that omits one fails at
construction naming the missing member). Domain imports are ``TYPE_CHECKING``
-only — see ``core/repository/README.md`` for why that direction is load-bearing.
"""
from __future__ import annotations

from abc import abstractmethod
from datetime import datetime
from typing import TYPE_CHECKING, Optional, Protocol, runtime_checkable

if TYPE_CHECKING:
    from agentclaw.community.core.task.domain.models import Status
    from agentclaw.community.core.task.repository.types import (
        TaskCallbackRecord,
        TaskInfoRecord,
        TaskNodeRecord,
        TaskNodeRelationRecord,
        TaskNodeRunInfoRecord,
        TaskNodeRunInfoUpdate,
    )


@runtime_checkable
class TaskInfoRepositoryProtocol(Protocol):
    """Durable store for the ``task_info`` table (task-level record)."""

    @abstractmethod
    def insert(self, record: "TaskInfoRecord") -> "TaskInfoRecord":
        """Insert one row keyed by ``task_id``. Raises ``IntegrityError`` on a
        duplicate ``task_id`` (no upsert — mirror ``task_queue`` insert-then-
        classify-conflict). Returns the stored record (with ``id``/``gmt_*``)."""
        ...

    @abstractmethod
    def get(self, task_id: str) -> Optional["TaskInfoRecord"]:
        """Return the row for ``task_id``, or ``None``."""
        ...

    @abstractmethod
    def update_status(self, task_id: str, status: "Status") -> bool:
        """Set ``status`` on the row for ``task_id``. Returns ``True`` iff a row
        was updated."""
        ...

    @abstractmethod
    def list_by_status(
        self,
        status: "Status",
        *,
        gmt_modified_since: Optional[datetime] = None,
        limit: int = 100,
    ) -> list["TaskInfoRecord"]:
        """Rows in ``status``, newest ``gmt_modified`` first (dashboard query)."""
        ...


@runtime_checkable
class TaskNodeRepositoryProtocol(Protocol):
    """Durable store for the ``task_node`` table (node spec + status)."""

    @abstractmethod
    def insert(self, record: "TaskNodeRecord") -> "TaskNodeRecord":
        """Insert one node row. A node is inserted once per ``(task_id, node_id)``."""
        ...

    @abstractmethod
    def get(self, task_id: str, node_id: str) -> Optional["TaskNodeRecord"]:
        """Return the node, or ``None``."""
        ...

    @abstractmethod
    def update_status(self, task_id: str, node_id: str, status: "Status") -> bool:
        """Set ``status`` on the node. Returns ``True`` iff a row was updated."""
        ...

    @abstractmethod
    def list_nodes(self, task_id: str) -> list["TaskNodeRecord"]:
        """All nodes for ``task_id``."""
        ...

    @abstractmethod
    def list_by_status(
        self,
        task_id: Optional[str],
        status: "Status",
        *,
        limit: int = 100,
    ) -> list["TaskNodeRecord"]:
        """Nodes in ``status``; optionally scoped to ``task_id``."""
        ...


@runtime_checkable
class TaskNodeRunInfoRepositoryProtocol(Protocol):
    """Durable store for ``task_node_run_info`` (1:N by ``retry`` per node)."""

    @abstractmethod
    def insert(self, record: "TaskNodeRunInfoRecord") -> "TaskNodeRunInfoRecord":
        """Insert one run-info row keyed by ``(task_id, node_id, retry)``. Raises
        ``IntegrityError`` on a duplicate of that triple."""
        ...

    @abstractmethod
    def update(
        self,
        task_id: str,
        node_id: str,
        retry: int,
        patch: "TaskNodeRunInfoUpdate",
    ) -> bool:
        """Apply the non-``None`` fields of ``patch`` to the row identified by
        ``(task_id, node_id, retry)``. Writes ``update_time`` when any field
        changes. Returns ``True`` iff a row was updated."""
        ...

    @abstractmethod
    def get_latest(self, task_id: str, node_id: str) -> Optional["TaskNodeRunInfoRecord"]:
        """Return the row with ``max(retry)`` for the node, or ``None``."""
        ...

    @abstractmethod
    def get_by_retry(
        self, task_id: str, node_id: str, retry: int
    ) -> Optional["TaskNodeRunInfoRecord"]:
        """Return the exact ``(task_id, node_id, retry)`` row, or ``None``."""
        ...

    @abstractmethod
    def list_by_task(self, task_id: str) -> list["TaskNodeRunInfoRecord"]:
        """All run-info rows for ``task_id``."""
        ...

    @abstractmethod
    def list_by_assignee(self, assignee: str, *, limit: int = 100) -> list["TaskNodeRunInfoRecord"]:
        """Rows whose ``assignee`` matches (backs ``idx_assignee``)."""
        ...

    @abstractmethod
    def list_by_run_mode(
        self,
        run_mode: str,
        *,
        start_time_since: Optional[int] = None,
        limit: int = 100,
    ) -> list["TaskNodeRunInfoRecord"]:
        """Rows in ``run_mode``; optionally ``start_time >= start_time_since``
        (backs ``idx_run_mode_status_time``; this table has no ``status`` column)."""
        ...


@runtime_checkable
class TaskNodeRelationRepositoryProtocol(Protocol):
    """Durable store for ``task_node_relation`` (the decomposition-tree edges)."""

    @abstractmethod
    def add_relations(self, records: list["TaskNodeRelationRecord"]) -> int:
        """Insert all edges. Raises ``IntegrityError`` on a duplicate
        ``(src_node_id, dst_node_id)``. Returns the count inserted."""
        ...

    @abstractmethod
    def list_relations(self, task_id: str) -> list["TaskNodeRelationRecord"]:
        """All edges for ``task_id``."""
        ...

    @abstractmethod
    def children(self, src_node_id: str) -> list["TaskNodeRelationRecord"]:
        """Edges whose parent is ``src_node_id``."""
        ...

    @abstractmethod
    def parents(self, dst_node_id: str) -> list["TaskNodeRelationRecord"]:
        """Edges whose child is ``dst_node_id``."""
        ...


@runtime_checkable
class TaskCallbackRepositoryProtocol(Protocol):
    """Append-only audit store for received task callbacks (``task_callback``)."""

    @abstractmethod
    def insert(self, rec: "TaskCallbackRecord") -> "TaskCallbackRecord":
        """Insert one callback row keyed by ``(run_id, node_id)``. Raises
        ``IntegrityError`` on a duplicate (``node_id`` is NOT NULL)."""
        ...

    @abstractmethod
    def get(self, run_id: str, node_id: str) -> Optional["TaskCallbackRecord"]:
        """Return the callback, or ``None``."""
        ...

    @abstractmethod
    def list_by_session(
        self, main_session_id: str, *, limit: int = 100
    ) -> list["TaskCallbackRecord"]:
        """Callbacks for ``main_session_id`` (backs ``idx_session_id``)."""
        ...