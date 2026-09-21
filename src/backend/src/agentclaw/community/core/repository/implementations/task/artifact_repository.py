"""``TaskArtifactRepositoryProtocol`` implementation for ``task_artifact``.

Append-only immutable manifest store (SQLite + OceanBase, single ORM body):
no update method exists by contract — content changes insert a new row whose
``supersedes`` points at the previous artifact.
"""
from __future__ import annotations

import json
from typing import Optional

from injector import inject

from agentclaw.community.core.repository.protocols.task import (
    TaskArtifactRepositoryProtocol,
)
from agentclaw.community.core.task.repository.models import TaskArtifactModel
from agentclaw.community.core.task.repository.types import ArtifactRecord
from agentclaw.community.plugin_api.database import DatabasePlugin


class TaskArtifactRepository(TaskArtifactRepositoryProtocol):
    """Unified ORM implementation for ``task_artifact`` (SQLite + OceanBase)."""

    @inject
    def __init__(self, db: DatabasePlugin) -> None:
        self._db = db
        self._model = TaskArtifactModel

    @staticmethod
    def _to_row(record: ArtifactRecord) -> TaskArtifactModel:
        return TaskArtifactModel(
            artifact_id=record.artifact_id,
            task_id=record.task_id,
            node_id=record.node_id,
            session_id=record.session_id,
            run_id=record.run_id,
            attempt=record.attempt,
            artifact_kind=record.artifact_kind,
            content=json.dumps(record.content),
            lineage=json.dumps(record.lineage) if record.lineage else None,
            supersedes=record.supersedes,
            created_by=json.dumps(record.created_by),
            created_at=record.created_at,
        )

    def insert(self, record: ArtifactRecord) -> ArtifactRecord:
        with self._db.orm_session() as db:
            row = self._to_row(record)
            db.add(row)
            db.flush()
            db.refresh(row)
            return row.to_record()

    def get(self, artifact_id: str) -> Optional[ArtifactRecord]:
        with self._db.orm_session() as db:
            row = (
                db.query(self._model)
                .filter(self._model.artifact_id == artifact_id)
                .first()
            )
            return row.to_record() if row else None

    def list_by_task(self, task_id: str) -> list[ArtifactRecord]:
        with self._db.orm_session() as db:
            rows = (
                db.query(self._model)
                .filter(self._model.task_id == task_id)
                .order_by(self._model.created_at.asc(), self._model.id.asc())
                .all()
            )
            return [r.to_record() for r in rows]

    def list_by_node(
        self, task_id: str, node_id: str, *, attempt: Optional[int] = None
    ) -> list[ArtifactRecord]:
        with self._db.orm_session() as db:
            q = db.query(self._model).filter(
                self._model.task_id == task_id,
                self._model.node_id == node_id,
            )
            if attempt is not None:
                q = q.filter(self._model.attempt == attempt)
            rows = (
                q.order_by(self._model.created_at.asc(), self._model.id.asc()).all()
            )
            return [r.to_record() for r in rows]

    def latest_by_node(
        self, task_id: str, node_id: str, *, attempt: Optional[int] = None
    ) -> Optional[ArtifactRecord]:
        with self._db.orm_session() as db:
            q = db.query(self._model).filter(
                self._model.task_id == task_id,
                self._model.node_id == node_id,
            )
            if attempt is not None:
                q = q.filter(self._model.attempt == attempt)
            row = (
                q.order_by(self._model.created_at.desc(), self._model.id.desc())
                .first()
            )
            return row.to_record() if row else None

    def list_superseding(self, artifact_id: str) -> list[ArtifactRecord]:
        with self._db.orm_session() as db:
            rows = (
                db.query(self._model)
                .filter(self._model.supersedes == artifact_id)
                .order_by(self._model.created_at.asc(), self._model.id.asc())
                .all()
            )
            return [r.to_record() for r in rows]