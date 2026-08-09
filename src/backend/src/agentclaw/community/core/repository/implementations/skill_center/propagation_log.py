"""Unified SkillPropagationLog repository (prod the relational store + local SQLite).

One ORM implementation behind ``SkillPropagationLogRepository``. The only
per-environment difference is the injected :class:`DatabasePlugin`:
``orm_session()`` yields a SQLAlchemy ``Session`` in both runtimes, so this
single body runs unchanged against OceanBase (prod) and SQLite (local).
That collapses the previous raw-SQL/ORM twins, so CI exercises the prod
path too.

Timestamps: production stores ``gmt_created``/``gmt_modified`` as
``varchar(64)`` DB-clock strings. Writes use ``func.now()`` (server
default / onupdate); ``find_recent`` anchors its window to the DB clock
(``SELECT func.now()``), subtracts in Python (the only non-portable bit),
and compares as a ``'YYYY-MM-DD HH:MM:SS'`` string — lexicographically =
chronologically ordered on both backends.
"""
from datetime import datetime, timedelta
from typing import Optional

from injector import inject
from sqlalchemy import func, select

from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.core.repository.protocols.skill_center import SkillPropagationLogRepository

_TS_FMT = "%Y-%m-%d %H:%M:%S"
_ALLOWED_UPDATE_FIELDS = {
    "status",
    "affected_bot_count",
    "success_bot_count",
    "failed_bot_ids",
    "extra",
    "error_msg",
}


def _as_datetime(db_now) -> datetime:
    """Normalise a ``func.now()`` scalar to ``datetime``.

    SQLite returns a ``'YYYY-MM-DD HH:MM:SS'`` string; mysqlconnector
    (OceanBase) returns a ``datetime``. Both reduce to one comparison
    anchor.
    """
    if isinstance(db_now, datetime):
        return db_now
    return datetime.strptime(str(db_now)[:19], _TS_FMT)


class SkillPropagationLogRepository(
    SkillPropagationLogRepository,
):
    """Unified ``SkillPropagationLogRepository`` Protocol implementation."""

    @inject
    def __init__(self, db: DatabasePlugin):
        from agentclaw.community.core.models.skill_propagation_log import (
            SkillPropagationLog,
        )

        self._db = db
        self.Model = SkillPropagationLog

    def create(self, data: dict) -> dict:
        with self._db.orm_session() as db:
            record = self.Model(**data)
            db.add(record)
            db.flush()
            # gmt_created/gmt_modified are server-side (func.now()); refresh
            # so the returned dict carries the DB-generated values.
            db.refresh(record)
            return record.to_dict()

    def update(self, propagation_id: str, data: dict) -> None:
        if not data:
            return
        changes = {
            k: v for k, v in data.items() if k in _ALLOWED_UPDATE_FIELDS
        }
        if not changes:
            return
        with self._db.orm_session() as db:
            record = (
                db.query(self.Model)
                .filter(self.Model.propagation_id == propagation_id)
                .first()
            )
            if record is None:
                return
            for k, v in changes.items():
                setattr(record, k, v)
            # gmt_modified updates via the column's onupdate=func.now();
            # no manual timestamp assignment.

    def find_recent(
        self, skill_uuid: str, env: str, within_seconds: int
    ) -> Optional[dict]:
        with self._db.orm_session() as db:
            db_now = db.execute(select(func.now())).scalar()
            cutoff = (
                _as_datetime(db_now) - timedelta(seconds=int(within_seconds))
            ).strftime(_TS_FMT)
            record = (
                db.query(self.Model)
                .filter(
                    self.Model.skill_uuid == skill_uuid,
                    self.Model.env == env,
                    self.Model.gmt_created >= cutoff,
                    self.Model.status.in_(["pending", "done"]),
                )
                .order_by(self.Model.gmt_created.desc())
                .first()
            )
            return record.to_dict() if record else None
