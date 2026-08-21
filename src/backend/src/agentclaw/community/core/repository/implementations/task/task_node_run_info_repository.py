"""``TaskNodeRunInfoRepositoryProtocol`` implementation for ``task_node_run_info``.

The table is 1:N by ``retry`` per ``(task_id, node_id)``; "latest" = ``max(retry)``.
"""
from __future__ import annotations

import json
import time
from typing import Any, Optional

from injector import inject

from agentclaw.community.core.repository.protocols.task import (
    TaskNodeRunInfoRepositoryProtocol,
)
from agentclaw.community.core.task.repository.models import TaskNodeRunInfoModel
from agentclaw.community.core.task.repository.types import (
    TaskNodeRunInfoRecord,
    TaskNodeRunInfoUpdate,
)
from agentclaw.community.plugin_api.database import DatabasePlugin


class TaskNodeRunInfoRepository(TaskNodeRunInfoRepositoryProtocol):
    """Unified ORM implementation for ``task_node_run_info`` (SQLite + OceanBase)."""

    @inject
    def __init__(self, db: DatabasePlugin) -> None:
        self._db = db
        self._model = TaskNodeRunInfoModel

    @staticmethod
    def _to_row(record: TaskNodeRunInfoRecord) -> TaskNodeRunInfoModel:
        return TaskNodeRunInfoModel(
            node_id=record.node_id,
            task_id=record.task_id,
            run_mode=record.run_mode,
            assignee=record.assignee,
            output=json.dumps(record.output) if record.output is not None else None,
            acceptance_result=(
                json.dumps(record.acceptance_result)
                if record.acceptance_result is not None
                else None
            ),
            retry=record.retry,
            session_id=record.session_id,
            extend_props=(
                json.dumps(record.extend_props)
                if record.extend_props is not None
                else None
            ),
            start_time=record.start_time,
            update_time=record.update_time,
            end_time=record.end_time,
        )

    def insert(self, record: TaskNodeRunInfoRecord) -> TaskNodeRunInfoRecord:
        with self._db.orm_session() as db:
            row = self._to_row(record)
            db.add(row)
            db.flush()
            db.refresh(row)
            return row.to_record()

    def update(
        self,
        task_id: str,
        node_id: str,
        retry: int,
        patch: TaskNodeRunInfoUpdate,
    ) -> bool:
        values: dict[str, Any] = {}
        if patch.run_mode is not None:
            values["run_mode"] = patch.run_mode
        if patch.assignee is not None:
            values["assignee"] = patch.assignee
        if patch.output is not None:
            values["output"] = json.dumps(patch.output)
        if patch.acceptance_result is not None:
            values["acceptance_result"] = json.dumps(patch.acceptance_result)
        if patch.session_id is not None:
            values["session_id"] = patch.session_id
        if patch.extend_props is not None:
            values["extend_props"] = json.dumps(patch.extend_props)
        if patch.start_time is not None:
            values["start_time"] = patch.start_time
        if patch.end_time is not None:
            values["end_time"] = patch.end_time
        if not values:
            return False
        # update_time: caller-supplied, else now-millis (the column has no DB default).
        values["update_time"] = (
            patch.update_time if patch.update_time is not None else int(time.time() * 1000)
        )
        with self._db.orm_session() as db:
            count = (
                db.query(self._model)
                .filter(
                    self._model.task_id == task_id,
                    self._model.node_id == node_id,
                    self._model.retry == retry,
                )
                .update(values, synchronize_session=False)
            )
        return count > 0

    def get_latest(self, task_id: str, node_id: str) -> Optional[TaskNodeRunInfoRecord]:
        with self._db.orm_session() as db:
            row = (
                db.query(self._model)
                .filter(self._model.task_id == task_id, self._model.node_id == node_id)
                .order_by(self._model.retry.desc())
                .first()
            )
            return row.to_record() if row else None

    def get_by_retry(
        self, task_id: str, node_id: str, retry: int
    ) -> Optional[TaskNodeRunInfoRecord]:
        with self._db.orm_session() as db:
            row = (
                db.query(self._model)
                .filter(
                    self._model.task_id == task_id,
                    self._model.node_id == node_id,
                    self._model.retry == retry,
                )
                .first()
            )
            return row.to_record() if row else None

    def list_by_task(self, task_id: str) -> list[TaskNodeRunInfoRecord]:
        with self._db.orm_session() as db:
            rows = db.query(self._model).filter(self._model.task_id == task_id).all()
            return [r.to_record() for r in rows]

    def list_by_assignee(
        self, assignee: str, *, limit: int = 100
    ) -> list[TaskNodeRunInfoRecord]:
        with self._db.orm_session() as db:
            rows = (
                db.query(self._model)
                .filter(self._model.assignee == assignee)
                .limit(limit)
                .all()
            )
            return [r.to_record() for r in rows]

    def list_by_run_mode(
        self,
        run_mode: str,
        *,
        start_time_since: Optional[int] = None,
        limit: int = 100,
    ) -> list[TaskNodeRunInfoRecord]:
        with self._db.orm_session() as db:
            q = db.query(self._model).filter(self._model.run_mode == run_mode)
            if start_time_since is not None:
                q = q.filter(self._model.start_time >= start_time_since)
            rows = q.limit(limit).all()
            return [r.to_record() for r in rows]