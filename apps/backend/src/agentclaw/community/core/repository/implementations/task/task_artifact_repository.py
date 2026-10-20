"""``TaskArtifactRepositoryProtocol`` implementation for ``task_artifact`` (read only).

The write path lives inside ``TaskGraphRepository.save_graph`` (inline write in
the graph transaction, spec 2026-10-20-task-artifact-domain §5.2) — this class
offers no INSERT/UPDATE; append-only means the manifest is only ever SELECTed.
"""
from __future__ import annotations

from injector import inject

from agentclaw.community.core.repository.protocols.task import (
    TaskArtifactRepositoryProtocol,
)
from agentclaw.community.core.task.repository.models import TaskArtifactModel
from agentclaw.community.core.task.repository.types import TaskArtifactRecord
from agentclaw.community.plugin_api.database import DatabasePlugin


class TaskArtifactRepository(TaskArtifactRepositoryProtocol):
    """Unified ORM read side for ``task_artifact`` (SQLite + OceanBase)."""

    @inject
    def __init__(self, db: DatabasePlugin) -> None:
        self._db = db
        self._model = TaskArtifactModel

    def list_by_task(
        self, task_id: str, *, limit: int = 200
    ) -> list[TaskArtifactRecord]:
        with self._db.orm_session() as db:
            rows = (
                db.query(self._model)
                .filter(self._model.task_id == task_id)
                .order_by(
                    self._model.attempt.desc(),
                    self._model.gmt_create.desc(),
                    self._model.id.desc(),
                )
                .limit(limit)
                .all()
            )
            return [r.to_record() for r in rows]

    def list_by_task_node(
        self, task_id: str, node_id: str, *, limit: int = 100
    ) -> list[TaskArtifactRecord]:
        with self._db.orm_session() as db:
            rows = (
                db.query(self._model)
                .filter(self._model.task_id == task_id, self._model.node_id == node_id)
                .order_by(
                    self._model.attempt.desc(),
                    self._model.gmt_create.desc(),
                    self._model.id.desc(),
                )
                .limit(limit)
                .all()
            )
            return [r.to_record() for r in rows]