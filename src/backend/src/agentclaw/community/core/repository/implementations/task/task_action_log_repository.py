"""Repository for the high-volume ``task_action_log`` table."""
from __future__ import annotations

import json
from typing import Optional

from injector import inject

from agentclaw.community.core.repository.protocols.task import (
    TaskActionLogRepositoryProtocol,
)
from agentclaw.community.core.task.domain.models import NodeAction, Status
from agentclaw.community.core.task.repository.models import TaskActionLogModel
from agentclaw.community.core.task.repository.types import TaskActionLogRecord
from agentclaw.community.plugin_api.database import DatabasePlugin


class TaskActionLogRepository(TaskActionLogRepositoryProtocol):
    """Bounded reads over append-only action history."""

    @inject
    def __init__(self, db: DatabasePlugin) -> None:
        self._db = db
        self._model = TaskActionLogModel

    @staticmethod
    def _to_record(row: TaskActionLogModel) -> TaskActionLogRecord:
        return TaskActionLogRecord(
            id=row.id,
            event_id=row.event_id,
            task_id=row.task_id,
            node_id=row.node_id,
            seq=row.seq,
            action=NodeAction(row.action),
            loop_round=row.loop_round,
            attempt=row.attempt,
            status_from=Status(row.status_from) if row.status_from else None,
            status_to=Status(row.status_to) if row.status_to else None,
            payload=json.loads(row.payload),
            instance_id=row.instance_id,
            gmt_create=row.gmt_create,
        )

    def append_many(self, events: list[TaskActionLogRecord]) -> int:
        if not events:
            return 0
        transaction = getattr(self._db, "transactional_orm_session", self._db.orm_session)
        inserted = 0
        with transaction() as db:
            for event in events:
                exists = (
                    db.query(self._model.id)
                    .filter(
                        (self._model.event_id == event.event_id)
                        | (
                            (self._model.task_id == event.task_id)
                            & (self._model.node_id == event.node_id)
                            & (self._model.seq == event.seq)
                        )
                    )
                    .first()
                )
                if exists is not None:
                    continue
                db.add(
                    self._model(
                        event_id=event.event_id,
                        task_id=event.task_id,
                        node_id=event.node_id,
                        seq=event.seq,
                        action=event.action.value,
                        loop_round=event.loop_round,
                        attempt=event.attempt,
                        status_from=(event.status_from.value if event.status_from else None),
                        status_to=(event.status_to.value if event.status_to else None),
                        payload=json.dumps(event.payload, ensure_ascii=False),
                        instance_id=event.instance_id,
                    )
                )
                inserted += 1
            db.flush()
        return inserted

    def list_by_task(
        self,
        task_id: str,
        *,
        node_id: Optional[str] = None,
        limit: int = 200,
        offset: int = 0,
    ) -> list[TaskActionLogRecord]:
        limit = max(1, min(limit, 200))
        offset = max(0, offset)
        with self._db.orm_session() as db:
            query = db.query(self._model).filter(self._model.task_id == task_id)
            if node_id is not None:
                query = query.filter(self._model.node_id == node_id)
            rows = (
                query.order_by(self._model.node_id.asc(), self._model.seq.asc())
                .offset(offset)
                .limit(limit)
                .all()
            )
            return [self._to_record(row) for row in rows]
