"""``TaskArtifactRepositoryProtocol`` implementation for ``task_artifact``.

Graph-driven rows are written inside ``TaskGraphRepository.save_graph`` (inline
write in the graph transaction, spec 2026-10-20-task-artifact-domain §5.2).
The API-driven write (manual artifact registration) goes through
:meth:`append` in its own transaction; the manifest stays append-only —
INSERT with select-first dedupe, no UPDATE path anywhere.
"""
from __future__ import annotations

from datetime import datetime

from injector import inject

from agentclaw.community.core.repository.protocols.task import (
    TaskArtifactRepositoryProtocol,
)
from agentclaw.community.core.task.repository.models import TaskArtifactModel
from agentclaw.community.core.task.repository.types import TaskArtifactRecord
from agentclaw.community.plugin_api.database import DatabasePlugin


class TaskArtifactRepository(TaskArtifactRepositoryProtocol):
    """Unified ORM read + API-write side for ``task_artifact`` (SQLite + OceanBase)."""

    @inject
    def __init__(self, db: DatabasePlugin) -> None:
        self._db = db
        self._model = TaskArtifactModel

    def _transaction(self):
        """Same seam as ``TaskGraphRepository._transaction``: prefer the
        profile's ``transactional_orm_session``, fall back to ``orm_session``."""

        method = getattr(self._db, "transactional_orm_session", None)
        return method() if method is not None else self._db.orm_session()

    def append(self, record: TaskArtifactRecord) -> TaskArtifactRecord:
        """Insert one artifact row in this repo's own transaction (spec §5.3).

        select-first dedupe on ``(task_id, node_id, attempt, kind,
        content_hash)`` mirrors the ``save_graph`` inline write: a duplicate
        hit is idempotent and returns the already-persisted record; any NULL
        key short-circuits the lookup (a tolerance row just inserts)."""

        from agentclaw.community.core.task.task_context.task_artifact import (
            content_to_json,
        )

        with self._transaction() as db:
            keys = (
                record.task_id,
                record.node_id,
                record.attempt,
                record.kind,
                record.content_hash,
            )
            if all(key is not None for key in keys):
                existing = (
                    db.query(self._model)
                    .filter(
                        self._model.task_id == record.task_id,
                        self._model.node_id == record.node_id,
                        self._model.attempt == record.attempt,
                        self._model.kind == record.kind,
                        self._model.content_hash == record.content_hash,
                    )
                    .first()
                )
                if existing is not None:
                    return existing.to_record()
            moment = datetime.fromtimestamp(record.created_at / 1000)
            db.add(TaskArtifactModel(
                artifact_id=record.artifact_id,
                artifact_name=record.artifact_name,
                task_id=record.task_id,
                node_id=record.node_id,
                attempt=record.attempt,
                scope_session_id=record.scope_session_id,
                kind=record.kind,
                storage=record.storage,
                content=content_to_json(record.content),
                content_hash=record.content_hash,
                creator=record.creator,
                modifier=record.creator,  # 行不可变:modifier 恒等于 creator
                gmt_create=moment,
                gmt_modify=moment,
            ))
            db.flush()
            return record

    def list_by_task(
        self, task_id: str, *, limit: int | None = 200
    ) -> list[TaskArtifactRecord]:
        with self._db.orm_session() as db:
            query = (
                db.query(self._model)
                .filter(self._model.task_id == task_id)
                .order_by(
                    self._model.attempt.desc(),
                    self._model.gmt_create.desc(),
                    self._model.id.desc(),
                )
            )
            if limit is not None:
                query = query.limit(limit)
            rows = query.all()
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

    def get_by_artifact_id(self, artifact_id: str) -> TaskArtifactRecord | None:
        with self._db.orm_session() as db:
            row = (
                db.query(self._model)
                .filter(self._model.artifact_id == artifact_id)
                .first()
            )
            return row.to_record() if row is not None else None
