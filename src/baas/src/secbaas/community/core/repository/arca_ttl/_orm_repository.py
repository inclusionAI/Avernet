"""ORM-based repository for baas_arca_ttl_renewal_schedule table.

Implements the registration slice of TtlRenewalScheduleRepository
(register / register_if_missing) via a shared dialect-specific atomic
upsert helper. The remaining protocol methods are expanded by a later
plan; the public signatures honor the enterprise raw-SQL semantics
clause-for-clause.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from secbaas.community.core.repository import OrmConnectionMixin, with_orm_session
from secbaas.community.logger import get_logger

from ._orm_model import TtlRenewalScheduleModel
from ._protocol import TtlRenewalScheduleRepository

log = get_logger("orm")


class OrmTtlRenewalScheduleRepository(OrmConnectionMixin, TtlRenewalScheduleRepository):
    def __init__(self, database) -> None:
        self._database = database

    def _upsert(self, **values):
        """Build the dialect-specific atomic upsert statement.

        Only ZDAS/MariaDB ("mysql") and SQLite dialects exist in the
        tree, so ``dialect.name == "sqlite"`` is the single branch point.
        The SET clauses mirror the enterprise ON DUPLICATE KEY UPDATE
        semantics item for item: ``sandbox_id = VALUES(sandbox_id),
        next_renew_at = VALUES(next_renew_at), status = 'ACTIVE',
        renew_fail_count = 0, gmt_modified = NOW()``.

        Dialect upserts do NOT apply ``Column.onupdate``, so
        ``gmt_modified`` must be set explicitly in both branches
        (Pitfall 2).
        """
        is_sqlite = self._session.bind.dialect.name == "sqlite"
        if is_sqlite:
            stmt = sqlite_insert(TtlRenewalScheduleModel).values(**values)
            return stmt.on_conflict_do_update(
                index_elements=["env", "source_table", "source_id"],
                set_={
                    "sandbox_id": stmt.excluded.sandbox_id,
                    "next_renew_at": stmt.excluded.next_renew_at,
                    "status": "ACTIVE",
                    "renew_fail_count": 0,
                    "gmt_modified": func.now(),
                },
            )
        stmt = mysql_insert(TtlRenewalScheduleModel).values(**values)
        return stmt.on_duplicate_key_update(
            sandbox_id=stmt.inserted.sandbox_id,
            next_renew_at=stmt.inserted.next_renew_at,
            status="ACTIVE",
            renew_fail_count=0,
            gmt_modified=func.now(),
        )

    @with_orm_session
    def register(
        self,
        env: str,
        sandbox_id: str,
        source_table: str,
        source_id: int,
        next_renew_at: datetime,
    ) -> None:
        """Register (or re-register) a container in the schedule table.

        Atomic upsert keyed on uk_source handles:
        - New containers (ACTIVE row created).
        - Re-created containers with the same source
          (STOPPED -> ACTIVE, fresh schedule, zeroed failure count).

        Args:
            env: Deployment environment ("pre", "prod", or "" for test).
            sandbox_id: Full ARCA paas_device_id as persisted in
                baas_device.provider_device_id — INCLUDES the @template_id
                suffix, stored verbatim.
            source_table: Source table name (e.g. "baas_device").
            source_id: Primary key in the source table.
            next_renew_at: Next scheduled renewal time.
        """
        log.info(
            "register: env=%s, sandbox_id=%s, source_table=%s, source_id=%s",
            env,
            sandbox_id,
            source_table,
            source_id,
        )
        stmt = self._upsert(
            env=env,
            sandbox_id=sandbox_id,
            source_table=source_table,
            source_id=source_id,
            next_renew_at=next_renew_at,
            status="ACTIVE",
            renew_fail_count=0,
        )
        self._session.execute(stmt)
        log.info("[arca_ttl:register] result: done")

    @with_orm_session
    def register_if_missing(
        self,
        env: str,
        sandbox_id: str,
        source_table: str,
        source_id: int,
        next_renew_at: datetime,
    ) -> None:
        """Register a container only if it is not already scheduled.

        The uk_source unique index carries the "if missing" semantic
        natively via the same atomic upsert: a new source inserts an
        ACTIVE row; a STOPPED row is resurrected to ACTIVE.

        Used during discovery scan / gap detection — the caller iterates
        over anti-join results and calls this method idempotently.
        """
        log.info(
            "register_if_missing: env=%s, sandbox_id=%s, source_table=%s, source_id=%s",
            env,
            sandbox_id,
            source_table,
            source_id,
        )
        stmt = self._upsert(
            env=env,
            sandbox_id=sandbox_id,
            source_table=source_table,
            source_id=source_id,
            next_renew_at=next_renew_at,
            status="ACTIVE",
            renew_fail_count=0,
        )
        self._session.execute(stmt)
        log.info("[arca_ttl:register_if_missing] result: done")
