"""Unified Quality Task repository (prod OceanBase + local SQLite).

One ORM implementation behind the ``QualityTaskRepository`` Protocol. The
only per-environment difference is the injected :class:`DatabasePlugin`:
``orm_session()`` yields a SQLAlchemy ``Session`` in both runtimes, so
this single body runs unchanged on OceanBase (prod) and SQLite (local),
collapsing the previous raw-SQL/ORM twins so CI exercises the prod path
too.
"""
from __future__ import annotations

import json
from typing import Any

from injector import inject
from sqlalchemy import func, update

from agentclaw.community.core.quality.models import QualityTaskRecord
from agentclaw.community.core.repository.implementations.bot.resource_locator import resource_locator, resource_owner_filter
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.utils.env_utils import get_current_env
from agentclaw.community.core.repository.protocols.platform import QualityTaskRepository as QualityTaskRepositoryProtocol


logger = get_logger()


def _merged_ext(current_ext: dict[str, Any], overlay: dict[str, Any] | None) -> str:
    """Merge ``overlay`` into the stored ext dict and serialize it back to JSON."""
    if overlay:
        current_ext.update(overlay)
    return json.dumps(current_ext, ensure_ascii=False)


def _row_to_record(row) -> QualityTaskRecord | None:
    """Convert ORM row to QualityTaskRecord dataclass."""
    if row is None:
        return None
    try:
        ext = (
            json.loads(row.ext)
            if isinstance(row.ext, str)
            else (row.ext or {})
        )
    except (json.JSONDecodeError, TypeError):
        ext = {}
    return QualityTaskRecord(
        id=row.id,
        uuid=row.uuid,
        task_type=row.task_type,
        biz_type=row.biz_type,
        status=row.status,
        bot_id=row.bot_id,
        owner_id=row.owner_id,
        ext=ext,
        operator_id=row.operator_id,
        env=row.env if row.env is not None else get_current_env(),
        gmt_create=row.gmt_create,
        gmt_modified=row.gmt_modified,
    )


class QualityTaskRepository(
    QualityTaskRepositoryProtocol,
):
    """Unified ``QualityTaskRepository`` Protocol implementation."""

    @inject
    def __init__(self, db: DatabasePlugin) -> None:
        from agentclaw.community.plugin_api.models import QualityTaskModel

        self._db = db
        self._Model = QualityTaskModel

    def list_by_conditions(
        self,
        *,
        task_type: str,
        biz_type: str,
        bot_id: str | None = None,
        owner_id: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[QualityTaskRecord], int]:
        """List quality tasks by conditions with pagination."""
        with self._db.orm_session() as session:
            if bot_id and owner_id:
                owner_id = resource_locator(session, bot_id, owner_id)
            query = session.query(self._Model).filter(
                self._Model.env == get_current_env(),
                self._Model.task_type == task_type,
                self._Model.biz_type == biz_type,
            )
            if bot_id:
                query = query.filter(self._Model.bot_id == bot_id)
            if owner_id:
                query = query.filter(
                    self._Model.owner_id == owner_id if bot_id else
                    resource_owner_filter(self._Model.bot_id, self._Model.owner_id, owner_id)
                )

            total = query.count()
            rows = (
                query.order_by(self._Model.gmt_create.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
                .all()
            )
            return [_row_to_record(r) for r in rows if _row_to_record(r) is not None], total

    def get_by_uuid(self, uuid: str) -> QualityTaskRecord | None:
        """Get a quality task by UUID."""
        with self._db.orm_session() as session:
            row = (
                session.query(self._Model)
                .filter(
                    self._Model.uuid == uuid,
                    self._Model.env == get_current_env(),
                )
                .first()
            )
            return _row_to_record(row)

    def get_by_id(self, id: int) -> QualityTaskRecord | None:
        """Get a quality task by ID."""
        with self._db.orm_session() as session:
            row = (
                session.query(self._Model)
                .filter(
                    self._Model.id == id,
                    self._Model.env == get_current_env(),
                )
                .first()
            )
            return _row_to_record(row)

    def create(
        self,
        *,
        uuid: str | None = None,
        task_type: str,
        biz_type: str,
        bot_id: str | None = None,
        owner_id: str | None = None,
        ext: dict[str, Any] | None = None,
        operator_id: str | None = None,
    ) -> QualityTaskRecord:
        """Create a new quality task."""
        with self._db.orm_session() as session:
            if bot_id and owner_id:
                owner_id = resource_locator(session, bot_id, owner_id)
            row = self._Model(
                uuid=uuid,
                task_type=task_type,
                biz_type=biz_type,
                status="init",
                bot_id=bot_id,
                owner_id=owner_id,
                ext=json.dumps(ext, ensure_ascii=False) if ext else "{}",
                operator_id=operator_id,
                env=get_current_env(),
            )
            session.add(row)
            session.flush()
            session.refresh(row)
            logger.info("[create] created quality task id=%s, uuid=%s, task_type=%s, biz_type=%s", row.id, row.uuid, row.task_type, row.biz_type)
            result = _row_to_record(row)
            if result is None:
                raise RuntimeError("Failed to convert created row to record")
            return result

    def update_status(
        self, id: int, status: str, ext: dict[str, Any] | None = None
    ) -> QualityTaskRecord | None:
        """Update the status of a quality task by ID.

        The standard-eval driver is spawned per pod, so several drivers can
        persist the same task state within the same second. Behind the
        production DB proxy such a no-op UPDATE reports 0 changed rows and
        the identity-checked ORM flush turns that into ``StaleDataError`` —
        killing every racing driver (CDO: ``expected to update 1 row(s); 0
        were matched`` on ``ac_bot_quality_task``). Issue the write as a
        Core UPDATE and read the row back instead: the racing write stays
        idempotent and callers still observe the persisted state.
        """
        with self._db.orm_session() as session:
            row = (
                session.query(self._Model)
                .filter(
                    self._Model.id == id,
                    self._Model.env == get_current_env(),
                )
                .first()
            )
            if not row:
                return None

            values: dict[str, Any] = {"status": status, "gmt_modified": func.now()}
            if ext is not None:
                try:
                    current_ext = (
                        json.loads(row.ext)
                        if isinstance(row.ext, str)
                        else (row.ext or {})
                    )
                except (json.JSONDecodeError, TypeError):
                    current_ext = {}
                values["ext"] = _merged_ext(current_ext, ext)

            session.execute(
                update(self._Model).where(self._Model.id == row.id).values(**values)
            )
            updated = _row_to_record(self._reselect(session, id))
            if updated is not None:
                logger.info(
                    "[update_status] updated quality task id=%s, status=%s",
                    updated.id,
                    updated.status,
                )
            else:
                logger.warning(
                    "[update_status] quality task id=%s vanished during update", id
                )
            return updated

    def update_ext(self, id: int, ext: dict[str, Any]) -> QualityTaskRecord | None:
        """Update only the ext field of a quality task by ID.

        Same Core-UPDATE rationale as :meth:`update_status`: concurrent
        drivers writing identical progress within one second must not raise
        ``StaleDataError`` from a 0-changed ORM flush.
        """
        with self._db.orm_session() as session:
            row = (
                session.query(self._Model)
                .filter(
                    self._Model.id == id,
                    self._Model.env == get_current_env(),
                )
                .first()
            )
            if not row:
                return None

            try:
                current_ext = (
                    json.loads(row.ext)
                    if isinstance(row.ext, str)
                    else (row.ext or {})
                )
            except (json.JSONDecodeError, TypeError):
                current_ext = {}

            session.execute(
                update(self._Model)
                .where(self._Model.id == row.id)
                .values(
                    gmt_modified=func.now(),
                    ext=_merged_ext(current_ext, ext),
                )
            )
            updated = _row_to_record(self._reselect(session, id))
            if updated is not None:
                logger.info("[update_ext] updated quality task id=%s", updated.id)
            else:
                logger.warning(
                    "[update_ext] quality task id=%s vanished during update", id
                )
            return updated

    def _reselect(self, session: Any, id: int) -> Any:
        """Re-read the task row after a Core UPDATE.

        The fresh SELECT synchronizes the identity map with what the racing
        writer actually persisted (or returns ``None`` if the row vanished),
        instead of asserting the UPDATE rowcount like an ORM flush does.
        """
        return (
            session.query(self._Model)
            .filter(
                self._Model.id == id,
                self._Model.env == get_current_env(),
            )
            .populate_existing()
            .first()
        )
