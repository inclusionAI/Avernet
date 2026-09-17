"""ORM models for the 5 collaboration-task tables.

Mirrors ``core/task_queue/repository/models.py``: ``Base`` from ``core/base``,
``with_variant(Integer, "sqlite")`` for autoincrement BIGINT PKs, ``utf8mb4_bin``
on identifier columns in unique keys (so OceanBase PAD-SPACE cannot merge two
distinct ids), and ``Index(..., unique=True)`` for unique keys. OceanBase-only
modifiers (``BLOCK_SIZE``/``LOCAL``/``GLOBAL``) are ORM-unrepresentable and live
only in ``core/task/sql/*.sql``.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects import mysql
from sqlalchemy.sql import func

from agentclaw.community.core.base import Base
from agentclaw.community.core.task.domain.models import RelationType, Status
from agentclaw.community.core.task.repository.types import (
    TaskCallbackCorrelationRecord,
    TaskCallbackRecord,
    TaskInfoRecord,
    TaskNodeRecord,
    TaskNodeRelationRecord,
    TaskNodeRunInfoRecord,
    TaskTrajectoryRecord,
    TrajectoryEventRecord,
)

# SQLite autoincrements only on INTEGER PRIMARY KEY; BigInteger renders BIGINT
# and breaks SQLite autoincrement. with_variant keeps BIGINT on MySQL/OceanBase.
AutoIncrementBigInteger = BigInteger().with_variant(Integer, "sqlite")


def _binary_string(length: int):
    """utf8mb4_bin on MySQL so unique-index comparisons are byte-for-byte
    (the default ci collation would fold distinct ids). SQLite stays plain."""
    return String(length).with_variant(
        mysql.VARCHAR(length, collation="utf8mb4_bin"), "mysql"
    )


_TASK_ID = _binary_string(128)
_NODE_ID = _binary_string(128)
_RUN_ID = _binary_string(512)
_SESSION_ID = _binary_string(256)
_ASSIGNEE = _binary_string(1024)
_USER_ID = _binary_string(256)


def _loads(text: Optional[str]) -> Optional[dict[str, Any]]:
    return json.loads(text) if text else None


class TaskInfoModel(Base):
    __tablename__ = "task_info"

    id = Column(
        AutoIncrementBigInteger, primary_key=True, autoincrement=True, nullable=False
    )
    task_id = Column(_TASK_ID, nullable=False)
    source_type = Column(String(128), nullable=False)
    owner_user_id = Column(_USER_ID, nullable=False)
    owner_bot_id = Column(_USER_ID, nullable=False)
    execution_config = Column(Text, nullable=True)
    task_spec = Column(Text, nullable=False)
    status = Column(String(64), nullable=False)
    graph_run_id = Column(_RUN_ID, nullable=True)
    graph_loop_round = Column(Integer, nullable=False, default=0)
    graph_output = Column(Text, nullable=True)
    graph_extend_props = Column(Text, nullable=True)
    graph_version = Column(BigInteger, nullable=False, default=0)
    lease_owner = Column(String(256), nullable=True)
    lease_until = Column(BigInteger, nullable=True)
    heartbeat_at = Column(BigInteger, nullable=True)
    gmt_create = Column(DateTime, default=func.now(), nullable=False)
    gmt_modified = Column(
        DateTime, default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        Index("uk_task_id", "task_id", unique=True),
        Index("idx_task_info_status", "status", "gmt_modified"),
    )

    def to_record(self) -> TaskInfoRecord:
        return TaskInfoRecord(
            id=self.id,
            task_id=self.task_id,
            source_type=self.source_type,
            owner_user_id=self.owner_user_id,
            owner_bot_id=self.owner_bot_id,
            execution_config=_loads(self.execution_config),
            task_spec=_loads(self.task_spec),
            status=Status(self.status),
            graph_run_id=self.graph_run_id,
            graph_loop_round=self.graph_loop_round,
            graph_output=_loads(self.graph_output),
            graph_extend_props=_loads(self.graph_extend_props),
            graph_version=self.graph_version,
            lease_owner=self.lease_owner,
            lease_until=self.lease_until,
            heartbeat_at=self.heartbeat_at,
            gmt_create=self.gmt_create,
            gmt_modified=self.gmt_modified,
        )


class TaskNodeModel(Base):
    __tablename__ = "task_node"

    id = Column(
        AutoIncrementBigInteger, primary_key=True, autoincrement=True, nullable=False
    )
    task_id = Column(_TASK_ID, nullable=False)
    node_id = Column(_NODE_ID, nullable=False)
    task_spec = Column(Text, nullable=False)
    status = Column(String(64), nullable=False)
    is_deleted = Column(Boolean, nullable=False, default=False, server_default="0")
    gmt_create = Column(DateTime, default=func.now(), nullable=False)
    gmt_modified = Column(
        DateTime, default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        Index("uk_task_node_identity", "task_id", "node_id", unique=True),
        Index("idx_task_status", "task_id", "status"),
    )

    def to_record(self) -> TaskNodeRecord:
        return TaskNodeRecord(
            id=self.id,
            task_id=self.task_id,
            node_id=self.node_id,
            task_spec=_loads(self.task_spec),
            status=Status(self.status),
            is_deleted=bool(self.is_deleted),
            gmt_create=self.gmt_create,
            gmt_modified=self.gmt_modified,
        )


class TaskNodeRunInfoModel(Base):
    __tablename__ = "task_node_run_info"

    id = Column(
        AutoIncrementBigInteger, primary_key=True, autoincrement=True, nullable=False
    )
    node_id = Column(_NODE_ID, nullable=False)
    task_id = Column(_TASK_ID, nullable=False)
    run_mode = Column(String(64), nullable=True)
    assignee = Column(_ASSIGNEE, nullable=True)
    output = Column(Text, nullable=True)
    acceptance_result = Column(Text, nullable=True)
    retry = Column(Integer, nullable=False, default=0)
    session_id = Column(_SESSION_ID, nullable=True)
    extend_props = Column(Text, nullable=True)
    start_time = Column(BigInteger, nullable=True)
    update_time = Column(BigInteger, nullable=True)
    end_time = Column(BigInteger, nullable=True)
    gmt_create = Column(DateTime, default=func.now(), nullable=False)
    gmt_modified = Column(
        DateTime, default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        Index("uk_task_node", "task_id", "node_id", "retry", unique=True),
        Index("idx_task", "task_id"),
        Index("idx_assignee", "assignee"),
        Index("idx_run_mode_status_time", "run_mode", "start_time"),
    )

    def to_record(self) -> TaskNodeRunInfoRecord:
        return TaskNodeRunInfoRecord(
            id=self.id,
            node_id=self.node_id,
            task_id=self.task_id,
            run_mode=self.run_mode,
            assignee=self.assignee,
            output=_loads(self.output),
            acceptance_result=_loads(self.acceptance_result),
            retry=self.retry,
            session_id=self.session_id,
            extend_props=_loads(self.extend_props),
            start_time=self.start_time,
            update_time=self.update_time,
            end_time=self.end_time,
            gmt_create=self.gmt_create,
            gmt_modified=self.gmt_modified,
        )


class TaskNodeRelationModel(Base):
    __tablename__ = "task_node_relation"

    id = Column(
        AutoIncrementBigInteger, primary_key=True, autoincrement=True, nullable=False
    )
    task_id = Column(_TASK_ID, nullable=False)
    src_node_id = Column(_NODE_ID, nullable=False)
    dst_node_id = Column(_NODE_ID, nullable=False)
    relation_type = Column(
        String(64), nullable=False, default=RelationType.DEPENDENCY.value
    )
    extend_props = Column(Text, nullable=True)
    gmt_create = Column(DateTime, default=func.now(), nullable=False)
    gmt_modified = Column(
        DateTime, default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        Index("uk_src_dst", "task_id", "src_node_id", "dst_node_id", unique=True),
        Index("idx_src", "task_id", "src_node_id"),
    )

    def to_record(self) -> TaskNodeRelationRecord:
        return TaskNodeRelationRecord(
            id=self.id,
            task_id=self.task_id,
            src_node_id=self.src_node_id,
            dst_node_id=self.dst_node_id,
            relation_type=RelationType(self.relation_type),
            extend_props=_loads(self.extend_props),
            gmt_create=self.gmt_create,
            gmt_modified=self.gmt_modified,
        )


class TaskCallbackModel(Base):
    __tablename__ = "task_callback"

    id = Column(
        AutoIncrementBigInteger, primary_key=True, autoincrement=True, nullable=False
    )
    invoker = Column(String(128), nullable=False)
    run_id = Column(_RUN_ID, nullable=False)
    node_id = Column(_NODE_ID, nullable=False)  # D5.1: NOT NULL, varchar(128)
    main_session_id = Column(_SESSION_ID, nullable=False)
    status = Column(String(64), nullable=True)
    orig_callback_data = Column(Text, nullable=False)
    execution_graph = Column(Text, nullable=True)
    result = Column(Text, nullable=True)
    result_success = Column(Boolean, nullable=True)
    exec_error = Column(Text, nullable=True)
    extend_props = Column(Text, nullable=True)
    event_id = Column(String(256), nullable=True)
    process_status = Column(String(64), nullable=True)
    processed_at = Column(DateTime, nullable=True)
    gmt_create = Column(DateTime, default=func.now(), nullable=False)
    gmt_modified = Column(
        DateTime, default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        Index("uk_workflow_instance", "run_id", "node_id", unique=True),
        Index("idx_session_id", "main_session_id"),
    )

    def to_record(self) -> TaskCallbackRecord:
        return TaskCallbackRecord(
            id=self.id,
            invoker=self.invoker,
            run_id=self.run_id,
            node_id=self.node_id,
            main_session_id=self.main_session_id,
            status=self.status,
            orig_callback_data=self.orig_callback_data,
            execution_graph=_loads(self.execution_graph),
            result=_loads(self.result),
            result_success=self.result_success,
            exec_error=self.exec_error,
            extend_props=_loads(self.extend_props),
            event_id=self.event_id,
            process_status=self.process_status,
            processed_at=self.processed_at,
            gmt_create=self.gmt_create,
            gmt_modified=self.gmt_modified,
        )


class TaskActionLogModel(Base):
    """Append-only high-volume node action history."""

    __tablename__ = "task_action_log"

    id = Column(
        AutoIncrementBigInteger, primary_key=True, autoincrement=True, nullable=False
    )
    event_id = Column(String(256), nullable=False)
    task_id = Column(_TASK_ID, nullable=False)
    node_id = Column(_NODE_ID, nullable=False)
    seq = Column(Integer, nullable=False)
    action = Column(String(64), nullable=False)
    loop_round = Column(Integer, nullable=True)
    attempt = Column(Integer, nullable=False, default=0)
    status_from = Column(String(64), nullable=True)
    status_to = Column(String(64), nullable=True)
    payload = Column(Text, nullable=False)
    instance_id = Column(String(256), nullable=True)
    gmt_create = Column(DateTime, default=func.now(), nullable=False)
    gmt_modified = Column(
        DateTime, default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        Index("uk_task_action_event", "event_id", unique=True),
        Index("uk_task_node_action_seq", "task_id", "node_id", "seq", unique=True),
        Index("idx_task_action_task_node", "task_id", "node_id", "seq"),
        Index("idx_task_action_created", "gmt_create"),
    )


class TaskTrajectoryModel(Base):
    """ORM for ``task_trajectory`` — one head row per task (UPSERT on assemble,
    UPDATE on analysis backfill).

    Independent trajectory entity: NO foreign key / NO association column to
    ``task_action_log`` or ``task_callback`` (spec invariant). ``gmt_create`` /
    ``gmt_modified`` are real stored columns (spec decision #7). For the head
    row, ``TaskTrajectoryRepository.upsert_head`` does NOT supply
    ``gmt_create``/``gmt_modified`` on insert and relies on the DB
    ``func.now()`` default (the head is not timeline-ordered); only the
    event-row path (``insert_event`` on ``TaskTrajectoryEventModel``) supplies
    ``gmt_create`` from the domain int-ms timestamp for timeline ordering.
    ``backfill_analysis`` sets ``gmt_modified`` explicitly in its UPDATE dict.
    """

    __tablename__ = "task_trajectory"

    id = Column(
        AutoIncrementBigInteger, primary_key=True, autoincrement=True, nullable=False
    )
    task_id = Column(_TASK_ID, nullable=False)
    analysis = Column(Text, nullable=True)
    # gmt_create 由 repo 从 domain int(ms) 转换写入,不依赖 DB DEFAULT。
    gmt_create = Column(DateTime, default=func.now(), nullable=False)
    gmt_modified = Column(
        DateTime, default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        Index("uk_task_trajectory_task", "task_id", unique=True),
    )

    def to_record(self) -> TaskTrajectoryRecord:
        return TaskTrajectoryRecord(
            id=self.id,
            task_id=self.task_id,
            analysis=self.analysis,
            gmt_create=self.gmt_create,
            gmt_modified=self.gmt_modified,
        )


class TaskTrajectoryEventModel(Base):
    """ORM for ``task_trajectory_events`` — append-only event projection rows
    (one row per gate emission; no unique constraint, duplicates accepted).

    Independent trajectory entity: NO foreign key / NO association column to
    ``task_action_log`` or ``task_callback`` (spec invariant). ``action_input``
    / ``ext_info`` / ``analysis`` are TEXT (raw strings — ``action_input`` is a
    digest/原文, ``ext_info`` is free JSON the domain does not map, ``analysis``
    is an embedded JSON string). ``gmt_create`` = event emission time (timeline
    ordering key); the repo (P1b) ALWAYS supplies it from the domain int-ms
    timestamp and does NOT rely on the DB ``DEFAULT CURRENT_TIMESTAMP`` (kept
    only as a fallback). The repo ``list_events_by_task`` MUST ``ORDER BY
    gmt_create ASC, id ASC`` for deterministic same-second timeline ordering.
    """

    __tablename__ = "task_trajectory_events"

    id = Column(
        AutoIncrementBigInteger, primary_key=True, autoincrement=True, nullable=False
    )
    task_id = Column(_TASK_ID, nullable=False)
    node_id = Column(_NODE_ID, nullable=False)
    action_type = Column(String(64), nullable=False)
    action_input = Column(Text, nullable=True)
    action_result = Column(Text, nullable=True)
    status_from = Column(String(64), nullable=True)
    status_to = Column(String(64), nullable=True)
    attempt = Column(Integer, nullable=False, default=0)
    error_type = Column(String(64), nullable=True)
    error_msg = Column(Text, nullable=True)
    ext_info = Column(Text, nullable=True)
    analysis = Column(Text, nullable=True)
    # gmt_create 由 repo 从 domain int(ms) 转换写入,不依赖 DB DEFAULT。
    gmt_create = Column(DateTime, default=func.now(), nullable=False)
    gmt_modified = Column(
        DateTime, default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        Index("idx_task_trajectory_events_task", "task_id", "gmt_create"),
        Index("idx_task_trajectory_events_node", "task_id", "node_id", "gmt_create"),
    )

    def to_record(self) -> TrajectoryEventRecord:
        return TrajectoryEventRecord(
            id=self.id,
            task_id=self.task_id,
            node_id=self.node_id,
            action_type=self.action_type,
            action_input=self.action_input,
            action_result=self.action_result,
            status_from=self.status_from,
            status_to=self.status_to,
            attempt=self.attempt,
            error_type=self.error_type,
            error_msg=self.error_msg,
            ext_info=self.ext_info,
            analysis=self.analysis,
            gmt_create=self.gmt_create,
            gmt_modified=self.gmt_modified,
        )


class TaskCallbackCorrelationModel(Base):
    """ORM for ``task_callback_correlation`` (REQ-P1) — persists the
    callback↔(task, node, retry) correlation so in-flight callbacks arriving
    after an instance restart can be re-attached to the right node by
    ``event_id`` (idempotent).

    Mirrors ``task_callback`` idiom (TaskCallbackModel): ``event_id`` plain
    ``String(256)`` for its unique key (matching task_callback.event_id), and
    ``_SESSION_ID`` / ``_TASK_ID`` / ``_NODE_ID`` binary-string helpers for the
    identifier columns. ``gmt_create`` is the only timestamp (no gmt_modified —
    this is an append-only correlation row);
    ``TaskCallbackCorrelationRepository.upsert_on_register`` does NOT supply
    ``gmt_create`` on insert and relies on the DB ``func.now()`` default (the
    correlation table is a lookup keyed by ``event_id``, not a
    timeline-ordered one).
    """

    __tablename__ = "task_callback_correlation"

    id = Column(
        AutoIncrementBigInteger, primary_key=True, autoincrement=True, nullable=False
    )
    event_id = Column(String(256), nullable=False)
    main_session_id = Column(_SESSION_ID, nullable=False)
    task_id = Column(_TASK_ID, nullable=False)
    node_id = Column(_NODE_ID, nullable=False)
    retry = Column(Integer, nullable=False, default=0)
    # gmt_create 由 repo 从 domain int(ms) 转换写入,不依赖 DB DEFAULT。
    gmt_create = Column(DateTime, default=func.now(), nullable=False)

    __table_args__ = (
        Index("uk_task_callback_correlation_event", "event_id", unique=True),
        Index("idx_task_callback_correlation_node", "task_id", "node_id"),
    )

    def to_record(self) -> TaskCallbackCorrelationRecord:
        return TaskCallbackCorrelationRecord(
            id=self.id,
            event_id=self.event_id,
            main_session_id=self.main_session_id,
            task_id=self.task_id,
            node_id=self.node_id,
            retry=self.retry,
            gmt_create=self.gmt_create,
        )
