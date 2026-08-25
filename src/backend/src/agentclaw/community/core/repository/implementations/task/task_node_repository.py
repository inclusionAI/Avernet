"""``TaskNodeRepositoryProtocol`` implementation for the ``task_node`` table."""
from __future__ import annotations

import json
from typing import Optional

from injector import inject

from agentclaw.community.core.repository.protocols.task import TaskNodeRepositoryProtocol
from agentclaw.community.core.task.domain.models import Status
from agentclaw.community.core.task.repository.models import TaskNodeModel
from agentclaw.community.core.task.repository.types import TaskNodeRecord
from agentclaw.community.plugin_api.database import DatabasePlugin


class TaskNodeRepository(TaskNodeRepositoryProtocol):
    """Unified ORM implementation for ``task_node`` (runs on SQLite + OceanBase)."""

    @inject
    def __init__(self, db: DatabasePlugin) -> None:
        self._db = db
        self._model = TaskNodeModel

    @staticmethod
    def _to_row(record: TaskNodeRecord) -> TaskNodeModel:
        return TaskNodeModel(
            task_id=record.task_id,
            node_id=record.node_id,
            task_spec=json.dumps(record.task_spec),
            status=record.status.value,
        )

    def insert(self, record: TaskNodeRecord) -> TaskNodeRecord:
        with self._db.orm_session() as db:
            row = self._to_row(record)
            db.add(row)
            db.flush()
            db.refresh(row)
            return row.to_record()

    def get(self, task_id: str, node_id: str) -> Optional[TaskNodeRecord]:
        with self._db.orm_session() as db:
            row = (
                db.query(self._model)
                .filter(self._model.task_id == task_id, self._model.node_id == node_id)
                .first()
            )
            return row.to_record() if row else None

    def update_status(self, task_id: str, node_id: str, status: Status) -> bool:
        with self._db.orm_session() as db:
            count = (
                db.query(self._model)
                .filter(
                    self._model.task_id == task_id,
                    self._model.node_id == node_id,
                )
                .update({"status": status.value}, synchronize_session=False)
            )
        return count > 0

    def list_nodes(self, task_id: str) -> list[TaskNodeRecord]:
        with self._db.orm_session() as db:
            rows = db.query(self._model).filter(self._model.task_id == task_id).all()
            return [r.to_record() for r in rows]

    def list_by_status(
        self,
        task_id: Optional[str],
        status: Status,
        *,
        limit: int = 100,
    ) -> list[TaskNodeRecord]:
        with self._db.orm_session() as db:
            q = db.query(self._model).filter(self._model.status == status.value)
            if task_id is not None:
                q = q.filter(self._model.task_id == task_id)
            rows = q.limit(limit).all()
            return [r.to_record() for r in rows]
