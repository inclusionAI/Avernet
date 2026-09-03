"""``TaskInfoRepositoryProtocol`` implementation for the ``task_info`` table."""
from __future__ import annotations

import json
from datetime import datetime
from typing import Optional, Sequence

from injector import inject

from agentclaw.community.core.repository.protocols.task import TaskInfoRepositoryProtocol
from agentclaw.community.core.task.domain.models import Status
from agentclaw.community.core.task.repository.models import TaskInfoModel
from agentclaw.community.core.task.repository.types import TaskInfoRecord
from agentclaw.community.plugin_api.database import DatabasePlugin


class TaskInfoRepository(TaskInfoRepositoryProtocol):
    """Unified ORM implementation for ``task_info`` (runs on SQLite + OceanBase)."""

    @inject
    def __init__(self, db: DatabasePlugin) -> None:
        self._db = db
        self._model = TaskInfoModel

    @staticmethod
    def _to_row(record: TaskInfoRecord) -> TaskInfoModel:
        return TaskInfoModel(
            task_id=record.task_id,
            source_type=record.source_type,
            owner_user_id=record.owner_user_id,
            owner_bot_id=record.owner_bot_id,
            execution_config=(
                json.dumps(record.execution_config)
                if record.execution_config is not None
                else None
            ),
            task_spec=json.dumps(record.task_spec),
            status=record.status.value,
            graph_run_id=record.graph_run_id,
            graph_loop_round=record.graph_loop_round,
            graph_output=json.dumps(record.graph_output) if record.graph_output is not None else None,
            graph_extend_props=(
                json.dumps(record.graph_extend_props)
                if record.graph_extend_props is not None
                else None
            ),
            graph_version=record.graph_version,
            lease_owner=record.lease_owner,
            lease_until=record.lease_until,
            heartbeat_at=record.heartbeat_at,
        )

    def insert(self, record: TaskInfoRecord) -> TaskInfoRecord:
        with self._db.orm_session() as db:
            row = self._to_row(record)
            db.add(row)
            db.flush()
            db.refresh(row)
            return row.to_record()

    def get(self, task_id: str) -> Optional[TaskInfoRecord]:
        with self._db.orm_session() as db:
            row = db.query(self._model).filter(self._model.task_id == task_id).first()
            return row.to_record() if row else None

    def update_status(self, task_id: str, status: Status) -> bool:
        with self._db.orm_session() as db:
            count = (
                db.query(self._model)
                .filter(self._model.task_id == task_id)
                .update({"status": status.value}, synchronize_session=False)
            )
        return count > 0

    def list_records(
        self,
        status: Optional[Sequence[Status]] = None,
        *,
        owner_user_id: Optional[str] = None,
    ) -> list[TaskInfoRecord]:
        with self._db.orm_session() as db:
            q = db.query(self._model)
            # status 是运行时态集合:空/None 不过滤,非空按 SQL IN 过滤。
            if status:
                q = q.filter(self._model.status.in_([s.value for s in status]))
            if owner_user_id is not None:
                q = q.filter(self._model.owner_user_id == owner_user_id)
            rows = q.order_by(
                self._model.gmt_modified.desc(),
                self._model.id.desc(),
            ).all()
            return [row.to_record() for row in rows]

    def list_records_page(
        self,
        status: Optional[Sequence[Status]] = None,
        *,
        owner_user_id: Optional[str] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[TaskInfoRecord], int]:
        with self._db.orm_session() as db:
            q = db.query(self._model)
            # status 是运行时态集合:空/None 不过滤,非空按 SQL IN 过滤。
            if status:
                q = q.filter(self._model.status.in_([s.value for s in status]))
            if owner_user_id is not None:
                q = q.filter(self._model.owner_user_id == owner_user_id)
            total = q.count()
            rows = (
                q.order_by(
                    self._model.gmt_modified.desc(),
                    self._model.id.desc(),
                )
                .offset((page - 1) * page_size)
                .limit(page_size)
                .all()
            )
            return [row.to_record() for row in rows], total

    def list_by_status(
        self,
        status: Status | Sequence[Status],
        *,
        gmt_modified_since: Optional[datetime] = None,
        limit: int = 100,
    ) -> list[TaskInfoRecord]:
        with self._db.orm_session() as db:
            statuses = [status] if isinstance(status, Status) else list(status)
            q = db.query(self._model).filter(
                self._model.status.in_([item.value for item in statuses])
            )
            if gmt_modified_since is not None:
                q = q.filter(self._model.gmt_modified >= gmt_modified_since)
            rows = q.order_by(self._model.gmt_modified.desc()).limit(limit).all()
            return [r.to_record() for r in rows]
