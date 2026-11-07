"""SQLAlchemy implementation of ``BotCopyRepositoryProtocol``.

One ORM body behind the protocol. The only per-environment difference is the
injected database plugin: ``orm_session()`` yields a SQLAlchemy ``Session`` in
both runtimes, so this single body runs unchanged on OceanBase (prod) and
SQLite (local) — the same "unified repository" pattern as
``core/repository/implementations/platform/task_queue``. No ``@plugin_impl``;
bound directly in DI.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any, Mapping, Sequence

from injector import inject
from sqlalchemy import update

from agentclaw.community.core.bot_copy.repository.models import AcBotCopyModel
from agentclaw.community.core.bot_copy.repository.protocol import (
    BotCopyRepositoryProtocol,
)
from agentclaw.community.core.bot_copy.types import BotCopyRow, BotCopyStatus
from agentclaw.community.plugin_api.database import DatabasePlugin


class BotCopyRepositoryImpl(BotCopyRepositoryProtocol):
    """The ledger operations against `ac_bot_copy`."""

    @inject
    def __init__(self, db: DatabasePlugin) -> None:
        self._db = db

    def insert(self, row: BotCopyRow) -> None:
        with self._db.orm_session() as db:
            db.add(
                AcBotCopyModel(
                    operation_id=row.operation_id,
                    source_bot_id=row.source_bot_id,
                    source_owner_id=row.source_owner_id,
                    dst_bot_id=row.dst_bot_id,
                    dst_owner_id=row.dst_owner_id,
                    operator_id=row.operator_id,
                    status=row.status.value,
                    request_detail=_dumps_json(row.request_detail),
                    progress_ext=_dumps_json(row.progress_ext),
                    retries=row.retries,
                    last_retry_at=row.last_retry_at,
                    last_error=row.last_error,
                    env=row.env,
                    avernet_tenant=row.avernet_tenant,
                )
            )
            db.flush()

    def get_by_operation_id(self, *, env: str, operation_id: str) -> BotCopyRow | None:
        with self._db.orm_session() as db:
            row = (
                db.query(AcBotCopyModel)
                .filter(AcBotCopyModel.env == env)
                .filter(AcBotCopyModel.operation_id == operation_id)
                .one_or_none()
            )
            return row.to_record() if row is not None else None

    def get_by_dst_bot_id(self, *, env: str, dst_bot_id: str) -> BotCopyRow | None:
        with self._db.orm_session() as db:
            row = (
                db.query(AcBotCopyModel)
                .filter(AcBotCopyModel.env == env)
                .filter(AcBotCopyModel.dst_bot_id == dst_bot_id)
                .one_or_none()
            )
            return row.to_record() if row is not None else None

    def get_many_by_dst_bot_ids(
        self, *, env: str, dst_bot_ids: Sequence[str]
    ) -> Mapping[str, BotCopyRow]:
        if not dst_bot_ids:
            return {}
        with self._db.orm_session() as db:
            rows = (
                db.query(AcBotCopyModel)
                .filter(AcBotCopyModel.env == env)
                .filter(AcBotCopyModel.dst_bot_id.in_(list(dst_bot_ids)))
                .all()
            )
            return {r.dst_bot_id: r.to_record() for r in rows}

    def cas_retry(
        self, *, env: str, operation_id: str, now: datetime, throttle_seconds: int = 60
    ) -> bool:
        """Throttled retry stamp: one UPDATE whose WHERE is the throttle CAS.

        Refuses terminal rows (CONVERGED / FAILED) and rows whose last retry
        landed inside the throttle window. ``rowcount == 1`` means the CAS took.
        """
        terminal_values = [s.value for s in BotCopyStatus.terminal()]
        throttle_before = now - timedelta(seconds=throttle_seconds)
        with self._db.orm_session() as db:
            stmt = (
                update(AcBotCopyModel)
                .where(AcBotCopyModel.env == env)
                .where(AcBotCopyModel.operation_id == operation_id)
                .where(AcBotCopyModel.status.notin_(terminal_values))
                .where(
                    (AcBotCopyModel.last_retry_at.is_(None))
                    | (AcBotCopyModel.last_retry_at < throttle_before)
                )
                .values(last_retry_at=now, retries=AcBotCopyModel.retries + 1)
            )
            result = db.execute(stmt)
            rowcount = result.rowcount or 0
            return rowcount == 1

    def update_progress_ext(
        self, *, env: str, operation_id: str, progress_ext: Mapping[str, Any]
    ) -> None:
        with self._db.orm_session() as db:
            stmt = (
                update(AcBotCopyModel)
                .where(AcBotCopyModel.env == env)
                .where(AcBotCopyModel.operation_id == operation_id)
                .values(progress_ext=_dumps_json(dict(progress_ext)))
            )
            db.execute(stmt)


def _dumps_json(obj: Mapping[str, Any] | None) -> str | None:
    if obj is None:
        return None
    return json.dumps(dict(obj), ensure_ascii=False)


__all__ = ["BotCopyRepositoryImpl"]
