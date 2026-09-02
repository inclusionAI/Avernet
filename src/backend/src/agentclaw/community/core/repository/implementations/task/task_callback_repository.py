"""``TaskCallbackRepositoryProtocol`` implementation for ``task_callback``."""
from __future__ import annotations

import json
from typing import Optional

from injector import inject

from agentclaw.community.core.repository.protocols.task import (
    TaskCallbackRepositoryProtocol,
)
from agentclaw.community.core.task.repository.models import TaskCallbackModel
from agentclaw.community.core.task.repository.types import TaskCallbackRecord
from agentclaw.community.plugin_api.database import DatabasePlugin


def _dumps(value) -> Optional[str]:
    return json.dumps(value) if value is not None else None


class TaskCallbackRepository(TaskCallbackRepositoryProtocol):
    """Unified ORM implementation for ``task_callback`` (SQLite + OceanBase)."""

    @inject
    def __init__(self, db: DatabasePlugin) -> None:
        self._db = db
        self._model = TaskCallbackModel

    @staticmethod
    def _to_row(record: TaskCallbackRecord) -> TaskCallbackModel:
        return TaskCallbackModel(
            invoker=record.invoker,
            run_id=record.run_id,
            node_id=record.node_id,
            main_session_id=record.main_session_id,
            status=record.status,
            orig_callback_data=record.orig_callback_data,
            execution_graph=_dumps(record.execution_graph),
            result=_dumps(record.result),
            result_success=record.result_success,
            exec_error=record.exec_error,
            extend_props=_dumps(record.extend_props),
            event_id=record.event_id,
            process_status=record.process_status,
            processed_at=record.processed_at,
        )

    def insert(self, rec: TaskCallbackRecord) -> TaskCallbackRecord:
        with self._db.orm_session() as db:
            row = self._to_row(rec)
            db.add(row)
            db.flush()
            db.refresh(row)
            return row.to_record()

    def upsert(self, rec: TaskCallbackRecord) -> TaskCallbackRecord:
        """按 ``(run_id, node_id)`` insert-or-update:已存在则覆盖可变列,否则 insert。
        回投可重放(start 后 result) → 刷新同一行,而非撞唯一键。"""
        with self._db.orm_session() as db:
            existing = (
                db.query(self._model)
                .filter(
                    self._model.run_id == rec.run_id,
                    self._model.node_id == rec.node_id,
                )
                .first()
            )
            if existing is not None:
                existing.status = rec.status
                existing.orig_callback_data = rec.orig_callback_data
                existing.execution_graph = _dumps(rec.execution_graph)
                existing.result = _dumps(rec.result)
                existing.result_success = rec.result_success
                existing.exec_error = rec.exec_error
                existing.extend_props = _dumps(rec.extend_props)
                existing.event_id = rec.event_id
                existing.process_status = rec.process_status
                existing.processed_at = rec.processed_at
                db.flush()
                return existing.to_record()
            row = self._to_row(rec)
            db.add(row)
            db.flush()
            db.refresh(row)
            return row.to_record()

    def upsert_error(self, rec: TaskCallbackRecord) -> TaskCallbackRecord:
        """解析失败兜底:按 ``(run_id, node_id)`` 仅更新 ``exec_error`` + ``extend_props``,
        保留既有行其它可变列(不覆盖 status/result/execution_graph 等);行不存在 → 插入。"""
        with self._db.orm_session() as db:
            existing = (
                db.query(self._model)
                .filter(
                    self._model.run_id == rec.run_id,
                    self._model.node_id == rec.node_id,
                )
                .first()
            )
            if existing is not None:
                existing.exec_error = rec.exec_error
                existing.extend_props = _dumps(rec.extend_props)
                db.flush()
                return existing.to_record()
            row = self._to_row(rec)
            db.add(row)
            db.flush()
            db.refresh(row)
            return row.to_record()

    def get(self, run_id: str, node_id: str) -> Optional[TaskCallbackRecord]:
        with self._db.orm_session() as db:
            row = (
                db.query(self._model)
                .filter(self._model.run_id == run_id, self._model.node_id == node_id)
                .first()
            )
            return row.to_record() if row else None

    def list_by_session(
        self, main_session_id: str, *, limit: int = 100
    ) -> list[TaskCallbackRecord]:
        with self._db.orm_session() as db:
            rows = (
                db.query(self._model)
                .filter(self._model.main_session_id == main_session_id)
                .limit(limit)
                .all()
            )
            return [r.to_record() for r in rows]

    def get_latest_by_session(self, main_session_id: str) -> Optional[TaskCallbackRecord]:
        """按 ``main_session_id`` 取最新一条回调(``gmt_modified`` desc、``id`` desc),供 dashboard 反查 execution_graph。"""
        with self._db.orm_session() as db:
            row = (
                db.query(self._model)
                .filter(self._model.main_session_id == main_session_id)
                .order_by(self._model.gmt_modified.desc(), self._model.id.desc())
                .first()
            )
            return row.to_record() if row else None

    def find_by_event_id(self, event_id: str) -> Optional[TaskCallbackRecord]:
        with self._db.orm_session() as db:
            row = (
                db.query(self._model)
                .filter(self._model.event_id == event_id)
                .first()
            )
            return row.to_record() if row else None
