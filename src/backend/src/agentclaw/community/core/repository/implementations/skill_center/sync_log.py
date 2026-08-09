"""Unified SkillCenterSyncLog repository (prod the relational store + local SQLite).

One ORM implementation behind ``SkillCenterSyncLogRepository``. The only
per-environment difference is the injected :class:`DatabasePlugin`:
``orm_session()`` yields a SQLAlchemy ``Session`` in both runtimes, so this
single body runs unchanged against OceanBase (prod) and SQLite (local),
collapsing the previous raw-SQL/ORM twins so CI exercises the prod path
too.

Timestamps are DB-clock strings: production ``gmt_created``/
``gmt_modified`` are ``varchar(64)`` (DDL-parity finding, same as the
pilot), so the model uses ``String(64)`` with
``server_default``/``onupdate`` = ``func.now()``. ``create()`` does
``flush()`` + ``refresh()`` to populate the server-generated values into
the returned dict. ``mark_success``/``mark_failed`` are single
conditional bulk ``UPDATE``s (atomic on both backends; the
``status='pending'`` guard stays in the ``WHERE``) and set
``gmt_modified = func.now()`` **explicitly** in the SET — a bulk
``Query.update()`` does not depend on the ORM-instance ``onupdate``
lifecycle hook the fetch-then-mutate path used. ``orm_session()``
persists on clean exit (AUTOCOMMIT on prod / commit on local) so writes
need no explicit commit. See
``ddl-parity-ac_skill_center_sync_log.md`` in the round-2 spec.
"""
from injector import inject
from sqlalchemy import func

from agentclaw.community.core.models.skill_center_sync_log import SkillCenterSyncLog
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.core.repository.protocols.skill_center import SkillCenterSyncLogRepository


class SkillCenterSyncLogRepository(
    SkillCenterSyncLogRepository,
):
    """Unified ``SkillCenterSyncLogRepository`` Protocol implementation."""

    @inject
    def __init__(self, db: DatabasePlugin):
        self._db = db

    def create(self, data: dict) -> dict:
        with self._db.orm_session() as db:
            record = SkillCenterSyncLog(**data)
            db.add(record)
            db.flush()
            # gmt_created/gmt_modified are server-side (func.now()) now;
            # refresh so the returned dict carries the DB values.
            db.refresh(record)
            return self._to_dict(record)

    def mark_success(
        self, skill_uuid: str, version: str, env: str, checksum: str = None
    ) -> None:
        # Single conditional UPDATE (atomic on both backends, incl. prod
        # AUTOCOMMIT) that keeps the status='pending' guard in the WHERE
        # — restores the prior prod twin's semantics that the fetch-then-
        # mutate ORM pattern had silently dropped (a row already
        # success/failed is a no-op; no read-modify-write race).
        with self._db.orm_session() as db:
            db.query(SkillCenterSyncLog).filter(
                SkillCenterSyncLog.skill_uuid == skill_uuid,
                SkillCenterSyncLog.version == version,
                SkillCenterSyncLog.env == env,
                SkillCenterSyncLog.status == "pending",
            ).update(
                {
                    SkillCenterSyncLog.status: "success",
                    SkillCenterSyncLog.checksum: checksum,
                    SkillCenterSyncLog.gmt_modified: func.now(),
                },
                synchronize_session=False,
            )

    def mark_failed(
        self, skill_uuid: str, version: str, env: str, error_msg: str
    ) -> None:
        with self._db.orm_session() as db:
            db.query(SkillCenterSyncLog).filter(
                SkillCenterSyncLog.skill_uuid == skill_uuid,
                SkillCenterSyncLog.version == version,
                SkillCenterSyncLog.env == env,
                SkillCenterSyncLog.status == "pending",
            ).update(
                {
                    SkillCenterSyncLog.status: "failed",
                    SkillCenterSyncLog.error_msg: error_msg,
                    SkillCenterSyncLog.gmt_modified: func.now(),
                },
                synchronize_session=False,
            )

    def find_latest(self, skill_uuid: str, env: str) -> dict | None:
        with self._db.orm_session() as db:
            row = (
                db.query(SkillCenterSyncLog)
                .filter(
                    SkillCenterSyncLog.skill_uuid == skill_uuid,
                    SkillCenterSyncLog.env == env,
                )
                .order_by(SkillCenterSyncLog.gmt_created.desc())
                .first()
            )
            return self._to_dict(row) if row else None

    @staticmethod
    def _to_dict(row: SkillCenterSyncLog) -> dict:
        return {
            "id": row.id,
            "skill_uuid": row.skill_uuid,
            "version": row.version,
            "env": row.env,
            "status": row.status,
            "checksum": row.checksum,
            "error_msg": row.error_msg,
            "extra": row.extra,
            "gmt_created": row.gmt_created,
            "gmt_modified": row.gmt_modified,
        }
