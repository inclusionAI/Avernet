"""ORM-based repository for baas_bot_ttl_renewal_schedule table.

Implements the full 11-method TtlRenewalScheduleRepository contract:
the registration slice (register / register_if_missing) via a shared
dialect-specific atomic upsert, the renewal scan (list_due_for_renewal)
as a pure-ORM LEFT JOIN against the two hot tables, row-level updates
(success / failure / postpone / set_status), counts, and the discovery
anti-join (find_unregistered) with dialect-aware JSON extraction.

Semantics honor the enterprise raw-SQL reference clause-for-clause;
binding-side hot-table queries carry no ``is_deleted`` filter because
production ``ac_entity_device_binding`` has no such column (D-16').
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import and_, func, literal, select, text, update
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from secbaas.community.core.repository import OrmConnectionMixin, with_orm_session
from secbaas.community.core.repository.device import DeviceModel
from secbaas.community.core.repository.device_binding import DeviceBindingModel
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

    def _json_unquote(self, col, path: str):
        """JSON value extraction with dialect-aware unquote (D-05').

        MySQL's ``JSON_EXTRACT`` returns quoted scalars and needs
        ``JSON_UNQUOTE``; SQLite's ``json_extract`` returns bare text.
        ZDAS and MariaDB drivers both report dialect name "mysql", so
        ``== "sqlite"`` is the single branch point. ``path`` is always a
        JSON path constant, never a user value.
        """
        expr = func.json_extract(col, text(f"'{path}'"))
        if self._session.bind.dialect.name == "sqlite":
            return expr
        return func.json_unquote(expr)

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

    @with_orm_session
    def list_due_for_renewal(
        self,
        env: str,
        source_table: str,
        limit: int = 500,
        *,
        now: datetime,
    ) -> list[dict]:
        """Query ACTIVE rows where next_renew_at < :now (caller-supplied).

        ``now`` must be a naive-UTC datetime computed by the caller
        (CR-01 clock domain): both sides of the comparison then share
        the UTC wall clock and the due gate is time-zone independent of
        the DB server clock (SQLite CURRENT_TIMESTAMP is UTC; MySQL
        NOW() follows the server time zone).

        LEFT JOINs the corresponding hot table to verify the container
        still exists and provide device_props for TTL extraction.
        Returns hot_id so the application layer can detect orphans
        (hot_id IS NULL).

        The JOIN is env-guarded (hot.env = :env in the ON clause), so a
        cold row matching a hot row from ANOTHER environment reports
        hot_id IS NULL and is treated as an orphan instead of being
        renewed cross-env.

        Two hot-table variants based on source_table:
          - "baas_device" -> LEFT JOIN baas_device (ON additionally requires
            is_deleted = 0, so a soft-deleted device reads as orphan)
          - "ac_entity_device_binding" -> LEFT JOIN ac_entity_device_binding
            (no is_deleted filter — production table has no such column, D-16')

        The cold side is restricted to the requested source_table
        (design doc §7.4: the scheduler issues two per-source_table
        calls via asyncio.gather). Without this predicate a binding
        row would come back from the device query with hot_id IS NULL
        and the scheduler's orphan detection would mark it STOPPED —
        every due row would be stopped via the other side's query.

        Returns:
            List of dicts, each with keys: id, sandbox_id, source_table,
            source_id, next_renew_at, renew_fail_count, device_props,
            hot_id.
        """
        if source_table == "baas_device":
            hot = DeviceModel
            device_props_col = DeviceModel.provider_device_props
            join_cond = and_(
                TtlRenewalScheduleModel.source_id == DeviceModel.id,
                TtlRenewalScheduleModel.source_table == "baas_device",
                DeviceModel.env == env,
                # is_deleted lives in the ON clause (not the WHERE): a
                # soft-deleted device must NOT satisfy the join, so its
                # cold row reports hot_id IS NULL and flows to orphan
                # handling instead of being renewed — same treatment
                # count_hot_arca_devices / find_unregistered apply.
                DeviceModel.is_deleted == 0,
            )
        elif source_table == "ac_entity_device_binding":
            hot = DeviceBindingModel
            device_props_col = DeviceBindingModel.device_props
            join_cond = and_(
                TtlRenewalScheduleModel.source_id == DeviceBindingModel.id,
                TtlRenewalScheduleModel.source_table == "ac_entity_device_binding",
                DeviceBindingModel.env == env,
            )
        else:
            raise ValueError(f"Unsupported source_table: {source_table}")

        stmt = (
            select(
                TtlRenewalScheduleModel.id,
                TtlRenewalScheduleModel.sandbox_id,
                TtlRenewalScheduleModel.source_table,
                TtlRenewalScheduleModel.source_id,
                TtlRenewalScheduleModel.next_renew_at,
                TtlRenewalScheduleModel.renew_fail_count,
                device_props_col.label("device_props"),
                hot.id.label("hot_id"),
            )
            .select_from(TtlRenewalScheduleModel)
            .outerjoin(hot, join_cond)
            .where(
                TtlRenewalScheduleModel.status == "ACTIVE",
                TtlRenewalScheduleModel.source_table == source_table,
                TtlRenewalScheduleModel.env == env,
                TtlRenewalScheduleModel.next_renew_at < now,
            )
            .order_by(TtlRenewalScheduleModel.next_renew_at.asc())
            .limit(limit)
        )
        result = self._session.execute(stmt)
        rows = [dict(row._mapping) for row in result]
        log.info("[arca_ttl:list_due_for_renewal] result: %s rows", len(rows))
        return rows

    @with_orm_session
    def update_after_success(
        self,
        env: str,
        source_table: str,
        source_id: int,
        next_renew_at: datetime,
    ) -> None:
        """Update schedule after a successful TTL renewal.

        Sets the next renewal time, resets the failure counter, and
        records the last successful renewal timestamp.
        """
        log.info(
            "update_after_success: env=%s, source_table=%s, source_id=%s",
            env,
            source_table,
            source_id,
        )
        stmt = (
            update(TtlRenewalScheduleModel)
            .where(
                TtlRenewalScheduleModel.env == env,
                TtlRenewalScheduleModel.source_table == source_table,
                TtlRenewalScheduleModel.source_id == source_id,
            )
            .values(
                next_renew_at=next_renew_at,
                renew_fail_count=0,
                last_renewed_at=func.now(),
                gmt_modified=func.now(),
            )
        )
        self._session.execute(stmt)
        log.info("[arca_ttl:update_after_success] result: done")

    @with_orm_session
    def update_after_failure(
        self,
        env: str,
        source_table: str,
        source_id: int,
        next_renew_at: datetime,
        new_fail_count: int,
    ) -> None:
        """Update schedule after a TTL renewal failure.

        Sets the retry time and the new failure count. The caller
        manages the increment logic (reads current count, increments,
        passes new value).
        """
        log.info(
            "update_after_failure: env=%s, source_table=%s, source_id=%s, "
            "new_fail_count=%s",
            env,
            source_table,
            source_id,
            new_fail_count,
        )
        stmt = (
            update(TtlRenewalScheduleModel)
            .where(
                TtlRenewalScheduleModel.env == env,
                TtlRenewalScheduleModel.source_table == source_table,
                TtlRenewalScheduleModel.source_id == source_id,
            )
            .values(
                next_renew_at=next_renew_at,
                renew_fail_count=new_fail_count,
                gmt_modified=func.now(),
            )
        )
        self._session.execute(stmt)
        log.info("[arca_ttl:update_after_failure] result: done")

    @with_orm_session
    def postpone_renewal(
        self,
        env: str,
        source_table: str,
        source_id: int,
        next_renew_at: datetime,
    ) -> None:
        """Reschedule renewal without recording a renewal event.

        Used when the scheduler postpones a container — updates
        next_renew_at and clears renew_fail_count, but does NOT set
        last_renewed_at because no renewal actually occurred.
        """
        log.info(
            "postpone_renewal: env=%s, source_table=%s, source_id=%s",
            env,
            source_table,
            source_id,
        )
        stmt = (
            update(TtlRenewalScheduleModel)
            .where(
                TtlRenewalScheduleModel.env == env,
                TtlRenewalScheduleModel.source_table == source_table,
                TtlRenewalScheduleModel.source_id == source_id,
            )
            .values(
                next_renew_at=next_renew_at,
                renew_fail_count=0,
                gmt_modified=func.now(),
            )
        )
        self._session.execute(stmt)
        log.info("[arca_ttl:postpone_renewal] result: done")

    @with_orm_session
    def count_active(self, env: str) -> int:
        """Count ACTIVE rows in the schedule table (gap detection).

        Used by the scheduler Step 0 gap detection — compares this
        count against hot-table ACTIVE ARCA row counts.
        """
        log.info("count_active: env=%s", env)
        stmt = (
            select(func.count())
            .select_from(TtlRenewalScheduleModel)
            .where(
                TtlRenewalScheduleModel.env == env,
                TtlRenewalScheduleModel.status == "ACTIVE",
            )
        )
        result = self._session.execute(stmt).scalar()
        log.info("[arca_ttl:count_active] result: %s", result)
        return result

    @with_orm_session
    def find_unregistered(
        self,
        env: str,
        side: str,
        limit: int = 500,
    ) -> list[dict]:
        """Discover ARCA containers in hot tables not yet registered in
        the schedule table (anti-join).

        Used by the discovery scan when gap detection finds hot > cold.
        Two variants based on side:
          - "baas_device" -> anti-join against baas_device
          - "ac_entity_device_binding" -> anti-join against
            ac_entity_device_binding

        The ON clause also matches s.sandbox_id against the hot row's
        current sandbox, so a stale ACTIVE cold row for an OLD sandbox
        (after a destroy+create swap) no longer suppresses the hot row,
        while a matching ACTIVE row still suppresses it. Both sides are
        env-scoped.

        Note: the binding side carries no is_deleted filter — production
        ac_entity_device_binding has no such column (D-16'). The device
        side keeps baas_device.is_deleted = 0.

        Returns:
            List of dicts, each with keys: id, sandbox_id,
            source_table, ttl.
        """
        if side == "baas_device":
            stmt = (
                select(
                    DeviceModel.id,
                    DeviceModel.provider_device_id.label("sandbox_id"),
                    literal("baas_device").label("source_table"),
                    self._json_unquote(
                        DeviceModel.provider_device_props,
                        "$.ttl_expiration_timestamp",
                    ).label("ttl"),
                )
                .select_from(DeviceModel)
                .outerjoin(
                    TtlRenewalScheduleModel,
                    and_(
                        TtlRenewalScheduleModel.source_table == "baas_device",
                        TtlRenewalScheduleModel.source_id == DeviceModel.id,
                        TtlRenewalScheduleModel.status == "ACTIVE",
                        TtlRenewalScheduleModel.env == env,
                        TtlRenewalScheduleModel.sandbox_id
                        == DeviceModel.provider_device_id,
                    ),
                )
                .where(
                    DeviceModel.provider_type == "ARCA",
                    DeviceModel.status == "ACTIVE",
                    DeviceModel.is_deleted == 0,
                    DeviceModel.env == env,
                    DeviceModel.provider_device_id.isnot(None),
                    TtlRenewalScheduleModel.id.is_(None),
                )
                .order_by(DeviceModel.id.asc())
                .limit(limit)
            )
        elif side == "ac_entity_device_binding":
            binding_sandbox = self._json_unquote(
                DeviceBindingModel.device_props, "$.sandbox_id"
            )
            stmt = (
                select(
                    DeviceBindingModel.id,
                    binding_sandbox.label("sandbox_id"),
                    literal("ac_entity_device_binding").label("source_table"),
                    self._json_unquote(
                        DeviceBindingModel.device_props,
                        "$.ttl_expiration_timestamp",
                    ).label("ttl"),
                )
                .select_from(DeviceBindingModel)
                .outerjoin(
                    TtlRenewalScheduleModel,
                    and_(
                        TtlRenewalScheduleModel.source_table
                        == "ac_entity_device_binding",
                        TtlRenewalScheduleModel.source_id == DeviceBindingModel.id,
                        TtlRenewalScheduleModel.status == "ACTIVE",
                        TtlRenewalScheduleModel.env == env,
                        TtlRenewalScheduleModel.sandbox_id == binding_sandbox,
                    ),
                )
                .where(
                    DeviceBindingModel.device_provider.in_(("arca", "ARCA")),
                    DeviceBindingModel.status == "ACTIVE",
                    DeviceBindingModel.env == env,
                    self._json_unquote(
                        DeviceBindingModel.device_props, "$.sandbox_id"
                    ).isnot(None),
                    TtlRenewalScheduleModel.id.is_(None),
                )
                .order_by(DeviceBindingModel.id.asc())
                .limit(limit)
            )
        else:
            raise ValueError(f"Unsupported side: {side}")

        result = self._session.execute(stmt)
        rows = [dict(row._mapping) for row in result]
        log.info("[arca_ttl:find_unregistered] result: %s rows", len(rows))
        return rows

    @with_orm_session
    def set_status(
        self,
        env: str,
        source_table: str,
        source_id: int,
        status: str,
    ) -> None:
        """Update the status of a schedule record.

        Called from stop / destroy hooks to mark STOPPED, and from the
        scheduler for orphan cleanup or max-fail threshold.
        """
        log.info(
            "set_status: env=%s, source_table=%s, source_id=%s, status=%s",
            env,
            source_table,
            source_id,
            status,
        )
        stmt = (
            update(TtlRenewalScheduleModel)
            .where(
                TtlRenewalScheduleModel.env == env,
                TtlRenewalScheduleModel.source_table == source_table,
                TtlRenewalScheduleModel.source_id == source_id,
            )
            .values(status=status, gmt_modified=func.now())
        )
        self._session.execute(stmt)
        log.info("[arca_ttl:set_status] result: done")

    @with_orm_session
    def count_hot_arca_devices(self, env: str) -> int:
        """Count ACTIVE ARCA devices in the baas_device hot table.

        Env-scoped: pre/prod share one MySQL instance, so this count is
        filtered by env to mirror the env-scoped count_active().
        """
        log.info("count_hot_arca_devices: env=%s", env)
        stmt = (
            select(func.count())
            .select_from(DeviceModel)
            .where(
                DeviceModel.provider_type == "ARCA",
                DeviceModel.status == "ACTIVE",
                DeviceModel.is_deleted == 0,
                DeviceModel.env == env,
                DeviceModel.provider_device_id.isnot(None),
            )
        )
        result = self._session.execute(stmt).scalar()
        log.info("[arca_ttl:count_hot_arca_devices] result: %s", result)
        return result

    @with_orm_session
    def count_hot_arca_bindings(self, env: str) -> int:
        """Count ACTIVE ARCA device bindings in ac_entity_device_binding.

        Env-scoped: pre/prod share one MySQL instance, so this count is
        filtered by env to mirror the env-scoped count_active().

        Note: no is_deleted filter — production
        ac_entity_device_binding has no such column (D-16').
        """
        log.info("count_hot_arca_bindings: env=%s", env)
        stmt = (
            select(func.count())
            .select_from(DeviceBindingModel)
            .where(
                DeviceBindingModel.device_provider.in_(("arca", "ARCA")),
                DeviceBindingModel.status == "ACTIVE",
                DeviceBindingModel.env == env,
                self._json_unquote(
                    DeviceBindingModel.device_props, "$.sandbox_id"
                ).isnot(None),
            )
        )
        result = self._session.execute(stmt).scalar()
        log.info("[arca_ttl:count_hot_arca_bindings] result: %s", result)
        return result
