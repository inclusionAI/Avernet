"""``TaskCallbackCorrelationRepositoryProtocol`` implementation for
``task_callback_correlation`` (REQ-P1).

Persists the callback↔(task, node, retry) correlation so in-flight callbacks
arriving after an instance restart can be re-attached to the right node by
``event_id`` (idempotent). The table is unique on ``event_id``; the repository
implements idempotency as query-then-return-existing-or-insert (DO NOT raise on
duplicate; do NOT mutate the stored correlation fields). Mirrors the sibling
``TaskCallbackRepository`` shape: ``@inject`` ctor with ``DatabasePlugin``,
``orm_session()`` / ``db.flush()`` / no explicit commit, ``to_record()`` for
detached frozen records.

The constructor signature follows the spec / plan exactly — ``gmt_create`` is
not a parameter (the table is a correlation/lookup table, not a timeline-
ordering one; the ORM ``func.now()`` default sets ``gmt_create`` on insert).
"""
from __future__ import annotations

from typing import Optional

from injector import inject

from agentclaw.community.core.repository.protocols.task import (
    TaskCallbackCorrelationRepositoryProtocol,
)
from agentclaw.community.core.task.repository.models import (
    TaskCallbackCorrelationModel,
)
from agentclaw.community.core.task.repository.types import (
    TaskCallbackCorrelationRecord,
)
from agentclaw.community.plugin_api.database import DatabasePlugin


class TaskCallbackCorrelationRepository(TaskCallbackCorrelationRepositoryProtocol):
    """Unified ORM implementation for ``task_callback_correlation`` (SQLite + OceanBase)."""

    @inject
    def __init__(self, db: DatabasePlugin) -> None:
        self._db = db
        self._model = TaskCallbackCorrelationModel

    def upsert_on_register(
        self,
        event_id: str,
        main_session_id: str,
        task_id: str,
        node_id: str,
        retry: int,
    ) -> TaskCallbackCorrelationRecord:
        """Idempotent on ``event_id``: if a row with ``event_id`` already exists,
        return it unchanged (DO NOT duplicate; do NOT mutate the stored
        correlation fields). Otherwise INSERT a new correlation row. ``gmt_create``
        is set by the ORM ``func.now()`` default on insert. Spec REQ-P1: "在
        TaskLoopCallback 注册时写、回调处理完按 event_id 幂等".
        """
        with self._db.orm_session() as db:
            existing = (
                db.query(self._model)
                .filter(self._model.event_id == event_id)
                .first()
            )
            if existing is not None:
                # idempotent: return existing unchanged (no mutation, no duplicate)
                return existing.to_record()
            row = self._model(
                event_id=event_id,
                main_session_id=main_session_id,
                task_id=task_id,
                node_id=node_id,
                retry=retry,
            )
            db.add(row)
            db.flush()
            db.refresh(row)
            return row.to_record()

    def find_by_event_id(
        self, event_id: str
    ) -> Optional[TaskCallbackCorrelationRecord]:
        with self._db.orm_session() as db:
            row = (
                db.query(self._model)
                .filter(self._model.event_id == event_id)
                .first()
            )
            return row.to_record() if row else None
