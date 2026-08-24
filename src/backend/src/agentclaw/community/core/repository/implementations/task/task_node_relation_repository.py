"""``TaskNodeRelationRepositoryProtocol`` implementation for ``task_node_relation``."""
from __future__ import annotations

import json

from injector import inject

from agentclaw.community.core.repository.protocols.task import (
    TaskNodeRelationRepositoryProtocol,
)
from agentclaw.community.core.task.repository.models import TaskNodeRelationModel
from agentclaw.community.core.task.repository.types import TaskNodeRelationRecord
from agentclaw.community.plugin_api.database import DatabasePlugin


class TaskNodeRelationRepository(TaskNodeRelationRepositoryProtocol):
    """Unified ORM implementation for ``task_node_relation`` (SQLite + OceanBase)."""

    @inject
    def __init__(self, db: DatabasePlugin) -> None:
        self._db = db
        self._model = TaskNodeRelationModel

    @staticmethod
    def _to_row(record: TaskNodeRelationRecord) -> TaskNodeRelationModel:
        return TaskNodeRelationModel(
            task_id=record.task_id,
            src_node_id=record.src_node_id,
            dst_node_id=record.dst_node_id,
            relation_type=record.relation_type.value,
            extend_props=(
                json.dumps(record.extend_props)
                if record.extend_props is not None
                else None
            ),
        )

    def add_relations(self, records: list[TaskNodeRelationRecord]) -> int:
        with self._db.orm_session() as db:
            for record in records:
                db.add(self._to_row(record))
            db.flush()
        return len(records)

    def list_relations(self, task_id: str) -> list[TaskNodeRelationRecord]:
        with self._db.orm_session() as db:
            rows = db.query(self._model).filter(self._model.task_id == task_id).all()
            return [r.to_record() for r in rows]

    def children(self, src_node_id: str) -> list[TaskNodeRelationRecord]:
        with self._db.orm_session() as db:
            rows = (
                db.query(self._model).filter(self._model.src_node_id == src_node_id).all()
            )
            return [r.to_record() for r in rows]

    def parents(self, dst_node_id: str) -> list[TaskNodeRelationRecord]:
        with self._db.orm_session() as db:
            rows = (
                db.query(self._model).filter(self._model.dst_node_id == dst_node_id).all()
            )
            return [r.to_record() for r in rows]
