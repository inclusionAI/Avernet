"""Session-bound writer for one durable PENDING task row.

The caller owns the surrounding transaction. This module owns every TaskQueue
storage detail: model construction, JSON serialization, DB-clock timestamps,
and active-only idempotency. It never commits or rolls back the outer session.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.sql.elements import ColumnElement

from agentclaw.community.core.task_queue.repository.models import TaskQueueModel
from agentclaw.community.core.task_queue.types import (
    TERMINAL_STATUSES,
    EnqueueResult,
    TaskRecord,
    TaskStatus,
)
from agentclaw.community.log import get_logger


logger = get_logger()

_ACTIVE_IDEM_INDEXES = (
    "uk_env_app_task_type_active_idempotency_key",
    "uk_env_task_type_active_idempotency_key",
)
_KEYED_INSERT_ATTEMPTS = 5
_MAX_IDEMPOTENCY_KEY_LEN = TaskQueueModel.__table__.c.idempotency_key.type.length
_MAX_TASK_TYPE_LEN = TaskQueueModel.__table__.c.task_type.type.length


def _validate_idempotency_key(key: str) -> None:
    """Reject values MySQL/OceanBase could pad or truncate into collisions."""
    if not isinstance(key, str) or not key.strip():
        raise ValueError(
            "idempotency_key must be a non-empty string; omit it entirely to opt out of dedup"
        )
    if key != key.strip():
        raise ValueError(
            f"idempotency_key must not have leading or trailing whitespace ({key!r}); "
            "MySQL/OceanBase PAD SPACE collations would merge distinct keys"
        )
    if len(key) > _MAX_IDEMPOTENCY_KEY_LEN:
        raise ValueError(
            f"idempotency_key exceeds {_MAX_IDEMPOTENCY_KEY_LEN} chars ({len(key)}); "
            "shorten it or hash the variable part"
        )


def _validate_keyed_task_type(task_type: str) -> None:
    """Protect the task-type scope columns of the active-key indexes."""
    if not isinstance(task_type, str) or task_type != task_type.strip():
        raise ValueError(
            f"task_type {task_type!r} must not have leading or trailing "
            "whitespace when an idempotency_key is supplied"
        )
    if len(task_type) > _MAX_TASK_TYPE_LEN:
        raise ValueError(
            f"task_type exceeds {_MAX_TASK_TYPE_LEN} chars ({len(task_type)}) "
            "and an idempotency_key was supplied"
        )


def _is_active_idem_conflict(exc: IntegrityError) -> bool:
    """Whether an engine-specific IntegrityError names the active-key index."""
    message = str(getattr(exc, "orig", None) or exc)
    if any(index in message for index in _ACTIVE_IDEM_INDEXES):
        return True
    return (
        "UNIQUE constraint failed" in message
        and "active_idempotency_key" in message
    )


class TaskQueuePendingRowWriter:
    """Insert or join one PENDING task inside an existing ORM transaction."""

    Model = TaskQueueModel

    @staticmethod
    def _now_plus(session: Any, seconds: float) -> ColumnElement:
        n = int(round(seconds))
        if n == 0:
            return func.now()
        if session.bind.dialect.name == "sqlite":
            return func.datetime(func.now(), text(f"'+{n} seconds'"))
        return func.date_add(func.now(), text(f"INTERVAL {n} SECOND"))

    def write_pending(
        self,
        session: Any,
        *,
        task_type: str,
        payload: dict,
        delay_seconds: int,
        deadline_seconds: int,
        env: str,
        app: str,
        idempotency_key: str | None = None,
    ) -> EnqueueResult:
        """Write without committing the caller-owned outer transaction."""
        if idempotency_key is not None:
            _validate_idempotency_key(idempotency_key)
            _validate_keyed_task_type(task_type)

        payload_json = json.dumps(payload, ensure_ascii=False)
        values = dict(
            task_type=task_type,
            payload_json=payload_json,
            delay_seconds=delay_seconds,
            deadline_seconds=deadline_seconds,
            env=env,
            app=app,
            idempotency_key=idempotency_key,
        )
        if idempotency_key is None:
            return EnqueueResult(self._insert(session, **values), True)

        for _ in range(_KEYED_INSERT_ATTEMPTS):
            try:
                # A duplicate must roll back only its INSERT. Rolling back the
                # outer session would discard the Publication/Materialization
                # facts this writer is specifically meant to share a UoW with.
                with session.begin_nested():
                    record = self._insert(session, **values)
            except IntegrityError as error:
                if not _is_active_idem_conflict(error):
                    raise
                holder = self._find_key_holder_current(
                    session,
                    env=env,
                    app=app,
                    task_type=task_type,
                    idempotency_key=idempotency_key,
                )
                if holder is not None and holder.status in TERMINAL_STATUSES:
                    raise RuntimeError(
                        f"[task_queue.enqueue] key={idempotency_key!r} "
                        f"type={task_type} env={env} app={app} is held by task "
                        f"id={holder.id}, which is terminal but never released "
                        "it. Retrying cannot help — the key is occupied "
                        "forever. Most likely a worker running code from "
                        "before enqueue idempotency wrote the terminal status "
                        "without clearing active_idempotency_key; clear that "
                        "column on the row to free the key"
                    )
                if holder is not None:
                    logger.info(
                        "[task_queue.enqueue] type=%s joined existing id=%s key=%s",
                        task_type,
                        holder.id,
                        idempotency_key,
                    )
                    return EnqueueResult(holder, False)
                continue
            return EnqueueResult(record, True)

        raise RuntimeError(
            "[task_queue.enqueue] could not insert or resolve a holder for "
            f"key={idempotency_key!r} type={task_type} env={env} app={app} "
            f"after {_KEYED_INSERT_ATTEMPTS} attempts; either the key was taken "
            "and released by other callers on every attempt, or another app "
            "holds it and the pre-app index "
            "uk_env_task_type_active_idempotency_key (which ignores app) is "
            "still on the table"
        )

    def _insert(
        self,
        session: Any,
        *,
        task_type: str,
        payload_json: str,
        delay_seconds: int,
        deadline_seconds: int,
        env: str,
        app: str,
        idempotency_key: str | None,
    ) -> TaskRecord:
        row = self.Model(
            task_type=task_type,
            payload=payload_json,
            status=TaskStatus.PENDING.value,
            run_at=self._now_plus(session, delay_seconds),
            deadline_at=self._now_plus(session, deadline_seconds),
            attempts=0,
            env=env,
            app=app,
            idempotency_key=idempotency_key,
            active_idempotency_key=idempotency_key,
        )
        session.add(row)
        session.flush()
        return row.to_record()

    def _key_holder_statement(
        self,
        *,
        env: str,
        app: str,
        task_type: str,
        idempotency_key: str,
    ):
        """Current/locking read for the unique-key holder.

        A Publication UoW may already have a REPEATABLE READ snapshot from
        locking Draft/Attempt rows. The unique index sees a concurrent holder
        even when that snapshot does not; FOR UPDATE is therefore required to
        resolve the conflict against current committed state. populate_existing
        prevents an older identity-map instance from winning over that read.
        """
        return (
            select(self.Model)
            .where(
                self.Model.env == env,
                self.Model.app == app,
                self.Model.task_type == task_type,
                self.Model.active_idempotency_key == idempotency_key,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )

    def _find_key_holder_current(
        self,
        session: Any,
        *,
        env: str,
        app: str,
        task_type: str,
        idempotency_key: str,
    ) -> TaskRecord | None:
        row = session.execute(
            self._key_holder_statement(
                env=env,
                app=app,
                task_type=task_type,
                idempotency_key=idempotency_key,
            )
        ).scalar_one_or_none()
        return row.to_record() if row is not None else None
