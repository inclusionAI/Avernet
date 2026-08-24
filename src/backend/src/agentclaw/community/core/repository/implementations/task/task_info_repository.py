"""``TaskInfoRepositoryProtocol`` implementation for the ``task_info`` table."""
from __future__ import annotations

import json
from datetime import datetime
from typing import Optional

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
        status: Optional[Status] = None,
        *,
        owner_user_id: Optional[str] = None,
    ) -> list[TaskInfoRecord]:
        with self._db.orm_session() as db:
            q = db.query(self._model)
            if status is not None:
                q = q.filter(self._model.status == status.value)
            if owner_user_id is not None:
                q = q.filter(self._model.owner_user_id == owner_user_id)
            rows = q.order_by(
                self._model.gmt_modified.desc(),
                self._model.id.desc(),
            ).all()
            return [row.to_record() for row in rows]

    def list_by_status(
        self,
        status: Status,
        *,
        gmt_modified_since: Optional[datetime] = None,
        limit: int = 100,
    ) -> list[TaskInfoRecord]:
        with self._db.orm_session() as db:
            q = db.query(self._model).filter(self._model.status == status.value)
            if gmt_modified_since is not None:
                q = q.filter(self._model.gmt_modified >= gmt_modified_since)
            rows = q.order_by(self._model.gmt_modified.desc()).limit(limit).all()
            return [r.to_record() for r in rows]
