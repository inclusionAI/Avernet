"""``TaskTrajectoryRepositoryProtocol`` implementation for the trajectory
旁路采集 tables (REQ-11).

Two tables, one repository:

* ``task_trajectory`` — one head row per task (unique on ``task_id``), UPSERT-ed
  on assemble, UPDATE-ed on analysis backfill.
* ``task_trajectory_events`` — append-only event projection rows (no unique
  constraint, duplicates accepted; ``ORDER BY gmt_create ASC, id ASC`` for
  deterministic same-second timeline recovery).

Independent entity: NO FK / NO association column to ``task_action_log`` or
``task_callback`` (spec invariant). All methods follow the sibling
``orm_session()`` / ``db.flush()`` / no-explicit-commit idiom; reads return
detached frozen records via the ORM models' ``to_record()``.

``gmt_create`` / ``gmt_modified`` are ``DateTime`` on the records. The
int↔datetime conversion is NOT done here — the P2 emitter supplies datetime on
emit (derived from the domain int-ms) and the P4 assembler reads datetime and
converts to int on read. ``insert_event`` preserves supplied ``gmt_create`` /
``gmt_modified`` values and fills any omitted values from one explicit
Asia/Shanghai wall clock. ``upsert_head`` likewise supplies one shared
Asia/Shanghai wall-clock value for both ``gmt_*`` fields.
``backfill_analysis`` sets ``gmt_modified`` EXPLICITLY in the UPDATE dict (do
NOT rely on the ORM ``onupdate=func.now()`` — bulk ``Query.update()`` bypasses
Python-side onupdate callbacks, so the column would otherwise be stale). All
application-supplied ``gmt_*`` values use the same Asia/Shanghai convention.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Optional

from injector import inject

from agentclaw.community.core.repository.protocols.task import (
    TaskTrajectoryRepositoryProtocol,
)
from agentclaw.community.core.task.repository.models import (
    TaskTrajectoryEventModel,
    TaskTrajectoryModel,
)
from agentclaw.community.core.task.repository.types import (
    TaskTrajectoryRecord,
    TrajectoryEventRecord,
)
from agentclaw.community.core.task.task_context.task_trajectory.time_utils import (
    storage_now,
)
from agentclaw.community.plugin_api.database import DatabasePlugin

logger = logging.getLogger("task.trajectory.repo")


class TaskTrajectoryRepository(TaskTrajectoryRepositoryProtocol):
    """Unified ORM implementation for ``task_trajectory`` / ``task_trajectory_events``
    (runs on SQLite + OceanBase)."""

    @inject
    def __init__(self, db: DatabasePlugin) -> None:
        self._db = db
        # one row per task (UPSERT-on-assemble head; unique on task_id)
        self._model = TaskTrajectoryModel
        # append-only event projection rows (no unique constraint)
        self._event_model = TaskTrajectoryEventModel

    # ------------------------------------------------------------------
    # insert_event (append-only, NO idempotency check)
    # ------------------------------------------------------------------

    @staticmethod
    def _to_event_row(record: TrajectoryEventRecord) -> TaskTrajectoryEventModel:
        """Build an event ORM row from a record.

        Supplied timestamps are preserved. If either is omitted, derive both
        from the available timestamp or one explicit Asia/Shanghai clock read;
        this avoids falling back to a database/session timezone. ``record.id``
        is ignored (autoincrement assigns the real id). ``ext_info`` /
        ``analysis`` are raw strings, inserted as-is.
        """
        fallback = record.gmt_create or record.gmt_modified or storage_now()
        kwargs = dict(
            task_id=record.task_id,
            node_id=record.node_id,
            action_type=record.action_type,
            action_input=record.action_input,
            action_result=record.action_result,
            status_from=record.status_from,
            status_to=record.status_to,
            attempt=record.attempt,
            boost_reason=record.boost_reason,
            error_type=record.error_type,
            error_msg=record.error_msg,
            ext_info=record.ext_info,
            analysis=record.analysis,
            gmt_create=record.gmt_create or fallback,
            gmt_modified=record.gmt_modified or fallback,
        )
        return TaskTrajectoryEventModel(**kwargs)

    def insert_event(self, record: TrajectoryEventRecord) -> TrajectoryEventRecord:
        with self._db.orm_session() as db:
            row = self._to_event_row(record)
            db.add(row)
            db.flush()
            db.refresh(row)
            return row.to_record()

    # ------------------------------------------------------------------
    # merge_last_event_ext_info (受限 UPDATE:do_analysis 会话明细物化专用)
    # ------------------------------------------------------------------

    def merge_last_event_ext_info(
        self,
        task_id: str,
        node_id: str,
        key: str,
        value: Any,
    ) -> bool:
        """Merge ``{key: value}`` into the (task_id, node_id) subtask's LAST
        event row's ``ext_info``(协议文档即契约:增量合并,不覆盖既有键;
        不可解析的 ext_info 原样保留返回 False)。

        最后一行的锚定:``ORDER BY gmt_create DESC, id DESC`` 的第一行(与组装层的
        正序排序对偶)。``gmt_create`` 不动(否则误动 do_analysis 的 timeline 指纹);
        ``gmt_modify`` 经 ORM ``onupdate`` 自然前进(指纹不含 gmt_modify,无碍)。
        返回 True=合并落库;False=行缺失/ext_info 非空但不可解析(不 clobber)。
        """
        import json as _json

        with self._db.orm_session() as db:
            row = (
                db.query(self._event_model)
                .filter(
                    self._event_model.task_id == task_id,
                    self._event_model.node_id == node_id,
                )
                .order_by(
                    self._event_model.gmt_create.desc(),
                    self._event_model.id.desc(),
                )
                .first()
            )
            if row is None:
                return False
            raw = row.ext_info
            if raw:
                try:
                    parsed = _json.loads(raw)
                except (ValueError, TypeError):
                    logger.warning(
                        "[task][trajectory-ext] ext_info 不可解析,拒绝增量合并(防覆盖)"
                        " task=%s node=%s last_event_id=%s",
                        task_id, node_id, row.id,
                    )
                    return False
                if not isinstance(parsed, dict):
                    logger.warning(
                        "[task][trajectory-ext] ext_info 非 JSON object,拒绝增量合并"
                        " task=%s node=%s last_event_id=%s",
                        task_id, node_id, row.id,
                    )
                    return False
            else:
                parsed = {}  # 无 ext_info → 建新信封
            merged = dict(parsed)
            merged[key] = value
            envelope = dict(merged)
            if "schema_v" not in envelope:
                # 与 emit 侧信封约定对齐({"schema_v": 1, **ext_info}_{!r} 是 emitter 包装形态)
                envelope = {"schema_v": 1, **envelope}
            row.ext_info = _json.dumps(envelope, ensure_ascii=False)
            db.flush()
            return True

    # ------------------------------------------------------------------
    # upsert_head (preserve existing analysis/gmt_modified)
    # ------------------------------------------------------------------

    def upsert_head(
        self,
        task_id: str,
        *,
        analysis: Optional[str] = None,
    ) -> TaskTrajectoryRecord:
        """Ensure the head row for ``task_id`` exists. If absent, insert a new
        head with the passed ``analysis`` (default ``None``) and fresh
        ``gmt_create``/``gmt_modified`` using the Asia/Shanghai storage
        convention; if present, return the existing record UNCHANGED — do NOT
        overwrite ``analysis`` or ``gmt_modified`` (spec: "已有行的
        analysis/gmt_modified
        不被覆盖").
        """
        with self._db.orm_session() as db:
            existing = (
                db.query(self._model)
                .filter(self._model.task_id == task_id)
                .first()
            )
            if existing is not None:
                # preserve existing analysis + gmt_modified — DO NOT mutate
                return existing.to_record()
            # Supply both values from one Beijing clock read. This avoids
            # mixing DB-local CURRENT_TIMESTAMP with an implicit host timezone.
            now = storage_now()
            row = self._model(
                task_id=task_id,
                analysis=analysis,
                gmt_create=now,
                gmt_modified=now,
            )
            db.add(row)
            db.flush()
            db.refresh(row)
            return row.to_record()

    # ------------------------------------------------------------------
    # backfill_analysis (two-table UPDATE; explicit gmt_modified)
    # ------------------------------------------------------------------

    def backfill_analysis(
        self,
        task_id: str,
        analysis_json: str,
        *,
        now: Optional[datetime] = None,
    ) -> int:
        """Set ``analysis=analysis_json`` and ``gmt_modified`` on BOTH
        ``task_trajectory`` and ``task_trajectory_events`` for ``task_id``.

        ``gmt_modified`` is set EXPLICITLY in the UPDATE dict to ``now`` (or
        the current Asia/Shanghai wall clock when omitted) — do NOT rely on
        the ORM ``onupdate=func.now()`` (bulk ``Query.update()`` bypasses
        Python-side onupdate callbacks). Never touches ``gmt_create``/typed columns/
        ``ext_info``. Returns total affected rows (head + events).

        Atomicity: the two-table UPDATE runs in one transaction on every
        profile. ``orm_session()`` runs at AUTOCOMMIT on the corp/OceanBase
        profile (see ``plugin_api/database.py``), so the two UPDATEs would NOT
        be atomic there on their own — we therefore prefer
        ``transactional_orm_session`` when available and fall back to
        ``orm_session`` on the SQLite test/local profile (mirrors the
        ``TaskActionLogRepository.append_many`` idiom). Spec REQ-9/REQ-11:
        both tables update together on backfill.
        """
        ts = now if now is not None else storage_now()
        transaction = getattr(
            self._db, "transactional_orm_session", self._db.orm_session
        )
        with transaction() as db:
            head_count = (
                db.query(self._model)
                .filter(self._model.task_id == task_id)
                .update(
                    {"analysis": analysis_json, "gmt_modified": ts},
                    synchronize_session=False,
                )
            )
            event_count = (
                db.query(self._event_model)
                .filter(self._event_model.task_id == task_id)
                .update(
                    {"analysis": analysis_json, "gmt_modified": ts},
                    synchronize_session=False,
                )
            )
            db.flush()
        return head_count + event_count

    # ------------------------------------------------------------------
    # list_events_by_task (ORDER BY gmt_create ASC, id ASC)
    # ------------------------------------------------------------------

    def list_events_by_task(self, task_id: str) -> list[TrajectoryEventRecord]:
        """Return all event rows for ``task_id`` ordered by ``gmt_create ASC,
        id ASC``. The ``id`` tiebreaker is REQUIRED for deterministic
        same-second ordering (``gmt_create`` is timestamp-second-precision and
        same-second ties are explicitly accepted by the spec)."""
        with self._db.orm_session() as db:
            rows = (
                db.query(self._event_model)
                .filter(self._event_model.task_id == task_id)
                .order_by(
                    self._event_model.gmt_create.asc(),
                    self._event_model.id.asc(),
                )
                .all()
            )
            return [r.to_record() for r in rows]

    # ------------------------------------------------------------------
    # list_head (read counterpart of upsert_head)
    # ------------------------------------------------------------------

    def list_head(self, task_id: str) -> Optional[TaskTrajectoryRecord]:
        """Return the head record for ``task_id`` or ``None`` when absent (the
        P4 assembler reads the head's persisted ``analysis``)."""
        with self._db.orm_session() as db:
            row = (
                db.query(self._model)
                .filter(self._model.task_id == task_id)
                .first()
            )
            return row.to_record() if row else None
