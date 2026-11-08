"""``task_config`` repository implementation (spec 2026-11-02).

One ORM body behind ``TaskConfigRepositoryProtocol``; the only per-dialect
difference is the upsert construct, mirroring
``core/repository/implementations/config/config.py``:

- SQLite: ``INSERT ... ON CONFLICT(config_name, env) DO UPDATE`` + follow-up
  PK SELECT (the only way to read the id back on SQLite without RETURNING).
- MySQL/OceanBase: ``INSERT ... ON DUPLICATE KEY UPDATE`` with
  ``id = LAST_INSERT_ID(id)`` so ``lastrowid`` returns the existing id on the
  update path.

The DO-update SET clause refreshes ``config_type`` / ``config_value`` /
``modifier`` / ``gmt_modify`` only — ``creator`` and ``gmt_create`` are first-write
values and must survive an overwrite (spec Anti-goal: upsert not read-modify-write;
set review-focus #3).

``config_value`` is passed in already-serialized JSON text; the repository does
not interpret it (the service encodes/validates per ``config_type``).
"""

from __future__ import annotations

from injector import inject
from sqlalchemy import func, text

from agentclaw.community.core.task.task_config.models import TaskConfigRecord
from agentclaw.community.core.task.task_config.orm import TaskConfigModel
from agentclaw.community.core.task.task_config.repository_protocol import (
    TaskConfigRepositoryProtocol,
)
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.database import DatabasePlugin

logger = get_logger()


class TaskConfigRepository(TaskConfigRepositoryProtocol):
    """ORM repository for ``task_config``; SQLite + MySQL/OceanBase parity."""

    @inject
    def __init__(self, db: DatabasePlugin):
        self._db = db

    def upsert(
        self,
        *,
        config_name: str,
        config_type: str,
        config_value: str,
        env: str,
        operator: str | None = None,
    ) -> int:
        with self._db.orm_session() as db:
            # get_bind() is the documented session→engine accessor in
            # SA 1.4 and 2.0. The branch is dialect-correctness only: both arms
            # perform the same atomic INSERT-or-update on uk_task_config_name_env;
            # there is no portable single-statement equivalent, so we dispatch
            # on dialect.name, never on runtime mode (spec constraint #5).
            dialect = db.get_bind().dialect.name
            table = TaskConfigModel.__table__
            values = {
                "config_name": config_name,
                "config_type": config_type,
                "config_value": config_value,
                "env": env,
                "creator": operator,
                "modifier": operator,
            }
            if dialect == "sqlite":
                from sqlalchemy.dialects.sqlite import insert as _insert

                stmt = _insert(table).values(**values)
                stmt = stmt.on_conflict_do_update(
                    index_elements=["config_name", "env"],
                    set_={
                        "config_type": stmt.excluded.config_type,
                        "config_value": stmt.excluded.config_value,
                        "modifier": stmt.excluded.modifier,
                        "gmt_modify": text("CURRENT_TIMESTAMP"),
                    },
                )
                db.execute(stmt)
                db.flush()
                config_id = (
                    db.query(TaskConfigModel.id)
                    .filter(
                        TaskConfigModel.config_name == config_name,
                        TaskConfigModel.env == env,
                    )
                    .scalar()
                )
            else:
                from sqlalchemy.dialects.mysql import insert as _insert

                stmt = _insert(table).values(**values)
                stmt = stmt.on_duplicate_key_update(
                    # LAST_INSERT_ID(id) propagates the existing id to lastrowid
                    # on the update path (mirror config.py upsert_category).
                    id=func.LAST_INSERT_ID(TaskConfigModel.id),
                    config_type=stmt.inserted.config_type,
                    config_value=stmt.inserted.config_value,
                    modifier=stmt.inserted.modifier,
                    gmt_modify=text("CURRENT_TIMESTAMP"),
                )
                config_id = db.execute(stmt).lastrowid
        logger.info(
            "[task_config.upsert] name=%s env=%s type=%s id=%s operator=%s",
            config_name, env, config_type, config_id, operator,
        )
        return int(config_id)

    def get_by_name(self, *, env: str, config_name: str) -> TaskConfigRecord | None:
        with self._db.orm_session() as db:
            row = (
                db.query(TaskConfigModel)
                .filter(
                    TaskConfigModel.env == env,
                    TaskConfigModel.config_name == config_name,
                )
                .first()
            )
            if row is None:
                return None
            return TaskConfigRecord(
                id=row.id,
                config_name=row.config_name,
                config_type=row.config_type,
                config_value=row.config_value,
                env=row.env,
                creator=row.creator,
                modifier=row.modifier,
                gmt_create=row.gmt_create,
                gmt_modify=row.gmt_modify,
            )
