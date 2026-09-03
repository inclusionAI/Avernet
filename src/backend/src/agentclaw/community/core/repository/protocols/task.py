"""Repository contracts for the collaboration-task persistence layer.

Every member is ``@abstractmethod`` (an implementation that omits one fails at
construction naming the missing member). Domain imports are ``TYPE_CHECKING``
-only — see ``core/repository/README.md`` for why that direction is load-bearing.
"""

from __future__ import annotations

from abc import abstractmethod
from datetime import datetime
from typing import TYPE_CHECKING, Optional, Protocol, Sequence, runtime_checkable

if TYPE_CHECKING:
    from agentclaw.community.core.task.domain.models import Status
    from agentclaw.community.core.task.repository.types import (
        BbsTaskOverviewRecord,
        TaskActionLogRecord,
        TaskCallbackRecord,
        TaskInfoRecord,
        TaskNodeRecord,
        TaskNodeRelationRecord,
        TaskNodeRunInfoRecord,
        TaskNodeRunInfoUpdate,
    )
    from agentclaw.community.core.task.task_discovery.lock_models import (
        TaskDiscoveryLockRecord,
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
    def list_records(
        self,
        status: Optional[Sequence["Status"]] = None,
        *,
        owner_user_id: Optional[str] = None,
    ) -> list["TaskInfoRecord"]:
        """Return newest-first records, optionally filtered by a set of statuses and owner."""
        ...

    @abstractmethod
    def list_records_page(
        self,
        status: Optional[Sequence["Status"]] = None,
        *,
        owner_user_id: Optional[str] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list["TaskInfoRecord"], int]:
        """Return one newest-first page (1-based, ``page_size`` items) plus total count.

        ``status`` 接受一组运行时态(Status 枚举)：空/None 不过滤，非空按 SQL IN 过滤。"""
        ...

    @abstractmethod
    def list_by_status(
        self,
        status: "Status | Sequence[Status]",
        *,
        gmt_modified_since: Optional[datetime] = None,
        limit: int = 100,
    ) -> list["TaskInfoRecord"]:
        """Rows in one or more statuses, newest ``gmt_modified`` first (dashboard query)."""
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
        status: "Status | Sequence[Status]",
        *,
        limit: int = 100,
    ) -> list["TaskNodeRecord"]:
        """Nodes in one or more statuses; optionally scoped to ``task_id``."""
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
    def get_latest(
        self, task_id: str, node_id: str
    ) -> Optional["TaskNodeRunInfoRecord"]:
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
    def list_by_assignee(
        self, assignee: str, *, limit: int = 100
    ) -> list["TaskNodeRunInfoRecord"]:
        """Rows whose ``assignee`` matches (backs ``idx_assignee``)."""
        ...

    @abstractmethod
    def get_by_session_id(self, session_id: str) -> Optional["TaskNodeRunInfoRecord"]:
        """按 BCS ``session_id`` 查 ``task_node_run_info``(BCN/ClawMind 回调收敛用)。

        ``task_node_run_info.session_id`` = BCS 建群/建 session 返的 session_id;
        BCN/ClawMind 回调的 ``main_session_id`` 同源。据此反查框架 (task_id, node_id)。
        """
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
    def upsert(self, rec: "TaskCallbackRecord") -> "TaskCallbackRecord":
        """Insert-or-refresh the callback row keyed by ``(run_id, node_id)``:
        an existing row's mutable columns are overwritten; absent → insert.
        Used by the callback receive path so replayable callbacks (start then
        result) refresh one row instead of raising on the unique key."""
        ...

    @abstractmethod
    def upsert_error(self, rec: "TaskCallbackRecord") -> "TaskCallbackRecord":
        """解析失败兜底落库:按 ``(run_id, node_id)`` **仅更新 ``exec_error`` + ``extend_props``**,
        保留既有行的其它字段(status/result/execution_graph/main_session_id 等不被覆盖);行不存在 → 插入。
        供 claw_mind 回调解析失败时兜底(错误信息落 exec_error、原始上报数据落 extend_props)。"""
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

    @abstractmethod
    def get_latest_by_session(
        self,
        main_session_id: str,
    ) -> Optional["TaskCallbackRecord"]:
        """Latest callback for ``main_session_id`` (``gmt_modified``/``id`` desc); ``None`` if absent.

        dashboard 按 root 节点的 BCS ``session_id`` 反查 ``task_callback.execution_graph`` 挂图级用。"""
        ...

    @abstractmethod
    def find_by_event_id(self, event_id: str) -> Optional["TaskCallbackRecord"]:
        """Return the callback row for ``event_id`` or ``None``.

        Backs event-idempotent callback handling: a non-``None`` row with
        ``process_status == "PROCESSED"`` means the event was already applied
        and must be acknowledged without replaying the graph mutation."""
        ...


@runtime_checkable
class TaskGraphRepositoryProtocol(Protocol):
    """Aggregate persistence contract for a complete task graph."""

    @abstractmethod
    def load_graph(self, task_id: str):
        """Hydrate a complete ``TaskExecutionGraph`` or return ``None``."""
        ...

    @abstractmethod
    def create_graph(self, graph, *, runtime_status: "Status"):
        """Persist initial graph state and return its version."""
        ...

    @abstractmethod
    def save_graph(
        self,
        graph,
        *,
        expected_version: int,
        runtime_status: "Status",
        action_events: list,
        instance_id: str | None = None,
        callback_audit: "TaskCallbackRecord | None" = None,
    ) -> int:
        """Persist graph state and action events with optimistic concurrency.

        When ``callback_audit`` is supplied, the inbound callback audit row is
        written (``process_status='PROCESSED'``) in the **same** transaction as
        the graph mutation, so the audit and the graph effect commit atomically
        and an already-processed ``event_id`` is never re-applied."""
        ...

    @abstractmethod
    def get_version(self, task_id: str) -> int | None:
        """Return the current graph version for a task."""
        ...

    @abstractmethod
    def next_action_seq(self, task_id: str, node_id: str) -> int:
        """Return the next append-only action sequence for a node."""
        ...

    @abstractmethod
    def load_action_logs(
        self,
        task_id: str,
        *,
        node_id: str | None = None,
        limit: int = 200,
    ) -> dict[str, list]:
        """Load bounded action history grouped by node for diagnostics."""
        ...

    @abstractmethod
    def list_recoverable(self, *, limit: int = 100) -> list[str]:
        """Return non-terminal tasks whose recovery lease is expired."""
        ...

    @abstractmethod
    def acquire_lease(
        self,
        task_id: str,
        *,
        instance_id: str,
        lease_seconds: int,
    ) -> bool:
        """Claim a recovery lease for one task."""
        ...

    @abstractmethod
    def heartbeat(
        self,
        task_id: str,
        *,
        instance_id: str,
        lease_seconds: int,
    ) -> bool:
        """Extend a lease owned by this instance."""
        ...

    @abstractmethod
    def release_lease(self, task_id: str, *, instance_id: str) -> bool:
        """Release a lease owned by this instance."""
        ...

    @abstractmethod
    def claim_bbs_owner(self, task_id: str, bot_id: str) -> bool:
        """Atomically claim the BBS relay root owner across instances.

        Returns ``True`` if ``bot_id`` now holds the claim (first claimer or
        idempotent re-claim), ``False`` if another bot already holds it.
        """
        ...

    @abstractmethod
    def release_bbs_owner(self, task_id: str, bot_id: str) -> bool:
        """Release a BBS relay claim held by ``bot_id``."""
        ...

    @abstractmethod
    def list_bbs_tasks_overview(
        self,
        page: int = 1,
        page_size: int = 20,
        *,
        search_word: str | None = None,
        status: str | None = None,
    ) -> "tuple[list[BbsTaskOverviewRecord], int]":
        """List one page (1-based) of BBS relay runs: ``task_node_run_info`` (run_mode='bbs')
        ⋈ ``task_node`` on (task_id, node_id), with ``task_info.owner_bot_id`` attached as
        ``publisher`` (batch-looked-up by task_id; missing task_info → ``None``). Returns
        ``(records, total)``: ``total`` is the filtered row count, ``records`` the stable
        ``(task_id, node_id)``-ordered page slice (LIMIT/OFFSET). Optional filters (None → no
        filter): ``status`` (single value, equals ``task_node.status``); ``search_word``
        (case-insensitive LIKE on ``task_node.task_spec`` or ``task_node_run_info.extend_props``).
        Read-only overview projection feeding ``GET /api/v1/collaboration/tasks/bbs/list``."""
        ...


@runtime_checkable
class TaskActionLogRepositoryProtocol(Protocol):
    """Append-only persistence contract for high-volume task actions."""

    @abstractmethod
    def append_many(self, events: list["TaskActionLogRecord"]) -> int:
        """Append action records atomically and return the inserted count."""
        ...

    @abstractmethod
    def list_by_task(
        self,
        task_id: str,
        *,
        node_id: str | None = None,
        limit: int = 200,
        offset: int = 0,
    ) -> list["TaskActionLogRecord"]:
        """Return bounded diagnostic action history."""
        ...


@runtime_checkable
class TaskDiscoveryLockRepositoryProtocol(Protocol):
    """Protocol for the task-discovery per-bot distributed lock repository.

    The lock is keyed on ``(env, bot_id, discovery_date)`` and backed by a
    UNIQUE constraint on ``ac_task_discovery_lock`` — that constraint is the
    guard. A single unified ORM body
    (``TaskDiscoveryLockRepository``) runs on both prod OceanBase and local
    SQLite via the injected DatabasePlugin.

    This is structurally identical to ``BotRestartLockRepositoryProtocol``,
    differing only in the lock key dimensions: ``discovery_date`` replaces
    ``entity_id``, and ``holder`` (hostname) replaces ``holder_user_id``.
    """

    @abstractmethod
    def acquire(
        self,
        env: str,
        bot_id: str,
        discovery_date: str,
        holder: str,
    ) -> Optional["TaskDiscoveryLockRecord"]:
        """Acquire the lock by inserting a row.

        Stamps a random ``lock_token`` (fencing token) on the row and returns
        the inserted record (carrying that token) on success, or ``None`` if a
        row for ``(env, bot_id, discovery_date)`` already exists (UNIQUE
        violation). The caller must keep the token and pass it to ``release``
        so a delete only ever removes the exact row it acquired.
        """
        ...

    @abstractmethod
    def get_if_stale(
        self,
        env: str,
        bot_id: str,
        discovery_date: str,
        ttl_seconds: int,
    ) -> Optional["TaskDiscoveryLockRecord"]:
        """Return the lock row only if it is older than ``ttl_seconds``.

        Staleness is evaluated DB-side (comparing ``gmt_create`` against the
        database clock) to avoid app/DB clock-skew. Returns ``None`` when no
        row exists or the existing row is still fresh.
        """
        ...

    @abstractmethod
    def release(
        self,
        env: str,
        bot_id: str,
        discovery_date: str,
        lock_token: str,
    ) -> bool:
        """Release the lock by hard-deleting the row — only if it's still ours.

        Compare-and-delete: ``DELETE WHERE (env, bot_id, discovery_date)
        matches AND lock_token = :lock_token``. The token guard prevents
        deleting a row that was reaped and re-acquired by another instance
        after this holder lost interest.
        """
        ...
