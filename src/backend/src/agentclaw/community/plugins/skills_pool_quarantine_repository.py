"""Migration Quarantine persistence mixed into the layout repository."""

from __future__ import annotations

import calendar
import json
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import exists, func, or_, text

from agentclaw.community.core.skills_pool.quarantine import (
    QuarantineRecord,
    RuntimeReconciliationStatus,
)
from agentclaw.community.core.skills_pool.repository.models import (
    BotSkillLayoutStateModel,
    SkillMigrationQuarantineModel,
)
from agentclaw.community.core.skills_pool.types import (
    BotSkillLayoutScope,
    SkillLayout,
    SkillLayoutPhase,
)


def _database_timestamp(
    value: datetime,
    *,
    dialect_name: str,
) -> object:
    """Express an aware instant in the database session timezone.

    MySQL ``TIMESTAMP`` columns are projected in the session timezone. Passing
    a UTC-aware value through PyMySQL drops the timezone without converting
    the wall clock, so compare and persist through ``FROM_UNIXTIME`` instead.
    SQLite's clock is UTC and accepts the equivalent UTC-naive value directly.
    """

    if value.tzinfo is None:
        return value
    utc_value = value.astimezone(UTC)
    if dialect_name in {"mysql", "mariadb"}:
        epoch_seconds = Decimal(calendar.timegm(utc_value.utctimetuple()))
        epoch_seconds += Decimal(utc_value.microsecond) / Decimal(1_000_000)
        return func.from_unixtime(epoch_seconds)
    return utc_value.replace(tzinfo=None)


class SkillsPoolQuarantineRepositoryMixin:
    """Generation-scoped evidence and cleanup audit operations."""

    _database: object

    def quarantine_identity_conflicts(
        self,
        *,
        scope: BotSkillLayoutScope,
        migration_generation: str,
        engine: str,
        path: str,
    ) -> bool:
        """Return whether a runtime identity contradicts durable evidence."""

        with self._database.transactional_orm_session() as session:
            existing = (
                session.query(SkillMigrationQuarantineModel)
                .filter(
                    SkillMigrationQuarantineModel.env == scope.env,
                    SkillMigrationQuarantineModel.entity_id == scope.entity_id,
                    SkillMigrationQuarantineModel.bot_id == scope.bot_id,
                    SkillMigrationQuarantineModel.migration_generation
                    == migration_generation,
                )
                .one_or_none()
            )
            return existing is not None and (
                existing.engine != engine or existing.path != path
            )

    @staticmethod
    def _cleanup_lease_deadline(session, seconds: int):
        if session.bind.dialect.name == "sqlite":
            return func.datetime(func.now(), text(f"'+{seconds} seconds'"))
        return func.date_add(func.now(), text(f"INTERVAL {seconds} SECOND"))

    @staticmethod
    def _upsert_quarantine(
        session,
        *,
        scope: BotSkillLayoutScope,
        migration_generation: str,
        engine: str,
        path: object,
        evidence_json: str,
    ) -> bool:
        existing = (
            session.query(SkillMigrationQuarantineModel)
            .filter(
                SkillMigrationQuarantineModel.env == scope.env,
                SkillMigrationQuarantineModel.entity_id == scope.entity_id,
                SkillMigrationQuarantineModel.bot_id == scope.bot_id,
                SkillMigrationQuarantineModel.migration_generation
                == migration_generation,
            )
            .one_or_none()
        )
        if existing is not None:
            return existing.engine == engine and (
                not isinstance(path, str) or not path or existing.path == path
            )
        if not isinstance(path, str) or not path:
            return False
        session.add(
            SkillMigrationQuarantineModel(
                env=scope.env,
                entity_id=scope.entity_id,
                bot_id=scope.bot_id,
                migration_generation=migration_generation,
                engine=engine,
                path=path,
                source_evidence=evidence_json,
            )
        )
        return True

    @staticmethod
    def _activate_quarantine(
        session,
        *,
        scope: BotSkillLayoutScope,
        migration_generation: str,
    ) -> bool:
        affected = (
            session.query(SkillMigrationQuarantineModel)
            .filter(
                SkillMigrationQuarantineModel.env == scope.env,
                SkillMigrationQuarantineModel.entity_id == scope.entity_id,
                SkillMigrationQuarantineModel.bot_id == scope.bot_id,
                SkillMigrationQuarantineModel.migration_generation
                == migration_generation,
                SkillMigrationQuarantineModel.pool_activated_at.is_(None),
            )
            .update(
                {SkillMigrationQuarantineModel.pool_activated_at: func.now()},
                synchronize_session=False,
            )
        )
        return affected == 1

    @staticmethod
    def _no_active_quarantine_cleanup(scope: BotSkillLayoutScope):
        return ~exists().where(
            SkillMigrationQuarantineModel.env == scope.env,
            SkillMigrationQuarantineModel.entity_id == scope.entity_id,
            SkillMigrationQuarantineModel.bot_id == scope.bot_id,
            SkillMigrationQuarantineModel.status == "cleaning",
            SkillMigrationQuarantineModel.cleanup_lease_expires_at > func.now(),
        )

    def get_quarantine(
        self,
        scope: BotSkillLayoutScope,
        migration_generation: str,
    ) -> QuarantineRecord | None:
        with self._database.transactional_orm_session() as session:
            row = (
                session.query(SkillMigrationQuarantineModel)
                .filter(
                    SkillMigrationQuarantineModel.env == scope.env,
                    SkillMigrationQuarantineModel.entity_id == scope.entity_id,
                    SkillMigrationQuarantineModel.bot_id == scope.bot_id,
                    SkillMigrationQuarantineModel.migration_generation
                    == migration_generation,
                )
                .one_or_none()
            )
            return row.to_record() if row and row.pool_activated_at else None

    def record_runtime_reconciliation(
        self,
        *,
        scope: BotSkillLayoutScope,
        migration_generation: str,
        observed_at: datetime,
        evidence: dict[str, object],
    ) -> bool:
        return self._record_runtime_reconciliation_result(
            scope=scope,
            migration_generation=migration_generation,
            observed_at=observed_at,
            status=RuntimeReconciliationStatus.READY,
            evidence=evidence,
        )

    def record_runtime_reconciliation_failure(
        self,
        *,
        scope: BotSkillLayoutScope,
        migration_generation: str,
        observed_at: datetime,
        evidence: dict[str, object],
    ) -> bool:
        return self._record_runtime_reconciliation_result(
            scope=scope,
            migration_generation=migration_generation,
            observed_at=observed_at,
            status=RuntimeReconciliationStatus.FAILED,
            evidence=evidence,
        )

    def _record_runtime_reconciliation_result(
        self,
        *,
        scope: BotSkillLayoutScope,
        migration_generation: str,
        observed_at: datetime,
        status: RuntimeReconciliationStatus,
        evidence: dict[str, object],
    ) -> bool:
        with self._database.transactional_orm_session() as session:
            observed_at_db = _database_timestamp(
                observed_at,
                dialect_name=session.bind.dialect.name,
            )
            timestamp_order = (
                SkillMigrationQuarantineModel.runtime_reconciled_at <= observed_at_db
                if status is RuntimeReconciliationStatus.FAILED
                else or_(
                    SkillMigrationQuarantineModel.runtime_reconciled_at
                    < observed_at_db,
                    (
                        SkillMigrationQuarantineModel.runtime_reconciled_at
                        == observed_at_db
                    )
                    & (
                        SkillMigrationQuarantineModel.runtime_reconciliation_status
                        == RuntimeReconciliationStatus.READY.value
                    ),
                )
            )
            current = (
                session.query(BotSkillLayoutStateModel.id)
                .filter(
                    BotSkillLayoutStateModel.env == scope.env,
                    BotSkillLayoutStateModel.entity_id == scope.entity_id,
                    BotSkillLayoutStateModel.bot_id == scope.bot_id,
                    BotSkillLayoutStateModel.active_layout == SkillLayout.POOL.value,
                    BotSkillLayoutStateModel.phase
                    == SkillLayoutPhase.POOL_ACTIVE.value,
                    BotSkillLayoutStateModel.migration_generation
                    == migration_generation,
                    BotSkillLayoutStateModel.pool_activated_at.isnot(None),
                )
                .first()
            )
            if current is None:
                return False
            quarantine = (
                session.query(SkillMigrationQuarantineModel.id)
                .filter(
                    SkillMigrationQuarantineModel.env == scope.env,
                    SkillMigrationQuarantineModel.entity_id == scope.entity_id,
                    SkillMigrationQuarantineModel.bot_id == scope.bot_id,
                    SkillMigrationQuarantineModel.migration_generation
                    == migration_generation,
                    SkillMigrationQuarantineModel.pool_activated_at.isnot(None),
                    SkillMigrationQuarantineModel.cleaned_at.is_(None),
                    SkillMigrationQuarantineModel.status != "cleaning",
                )
                .first()
            )
            if quarantine is None:
                return False
            post_activation = (
                session.query(BotSkillLayoutStateModel.id)
                .filter(
                    BotSkillLayoutStateModel.id == current.id,
                    BotSkillLayoutStateModel.pool_activated_at < observed_at_db,
                )
                .first()
            )
            if post_activation is None:
                return True
            affected = (
                session.query(SkillMigrationQuarantineModel)
                .filter(
                    SkillMigrationQuarantineModel.env == scope.env,
                    SkillMigrationQuarantineModel.entity_id == scope.entity_id,
                    SkillMigrationQuarantineModel.bot_id == scope.bot_id,
                    SkillMigrationQuarantineModel.migration_generation
                    == migration_generation,
                    SkillMigrationQuarantineModel.pool_activated_at < observed_at_db,
                    SkillMigrationQuarantineModel.cleaned_at.is_(None),
                    SkillMigrationQuarantineModel.status != "cleaning",
                    or_(
                        SkillMigrationQuarantineModel.runtime_reconciled_at.is_(None),
                        timestamp_order,
                    ),
                )
                .update(
                    {
                        SkillMigrationQuarantineModel.runtime_reconciled_at: (
                            observed_at_db
                        ),
                        SkillMigrationQuarantineModel.runtime_reconciliation_status: (
                            status.value
                        ),
                        SkillMigrationQuarantineModel.runtime_evidence: json.dumps(
                            evidence,
                            ensure_ascii=False,
                        ),
                    },
                    synchronize_session=False,
                )
            )
            if affected == 1:
                return True
            superseded = (
                session.query(SkillMigrationQuarantineModel.id)
                .filter(
                    SkillMigrationQuarantineModel.env == scope.env,
                    SkillMigrationQuarantineModel.entity_id == scope.entity_id,
                    SkillMigrationQuarantineModel.bot_id == scope.bot_id,
                    SkillMigrationQuarantineModel.migration_generation
                    == migration_generation,
                    SkillMigrationQuarantineModel.pool_activated_at < observed_at_db,
                    SkillMigrationQuarantineModel.cleaned_at.is_(None),
                    SkillMigrationQuarantineModel.status != "cleaning",
                    SkillMigrationQuarantineModel.runtime_reconciled_at
                    >= observed_at_db,
                )
                .first()
            )
            return superseded is not None

    def claim_cleanup(
        self,
        *,
        scope: BotSkillLayoutScope,
        migration_generation: str,
        cleanup_owner: str,
        lease_seconds: int,
        eligible_before: datetime,
    ) -> bool:
        with self._database.transactional_orm_session() as session:
            eligible_before_db = _database_timestamp(
                eligible_before,
                dialect_name=session.bind.dialect.name,
            )
            current = (
                session.query(BotSkillLayoutStateModel)
                .filter(
                    BotSkillLayoutStateModel.env == scope.env,
                    BotSkillLayoutStateModel.entity_id == scope.entity_id,
                    BotSkillLayoutStateModel.bot_id == scope.bot_id,
                    BotSkillLayoutStateModel.active_layout == SkillLayout.POOL.value,
                    BotSkillLayoutStateModel.phase
                    == SkillLayoutPhase.POOL_ACTIVE.value,
                    BotSkillLayoutStateModel.migration_generation
                    == migration_generation,
                )
                .with_for_update()
                .one_or_none()
            )
            if current is None:
                return False
            affected = (
                session.query(SkillMigrationQuarantineModel)
                .filter(
                    SkillMigrationQuarantineModel.env == scope.env,
                    SkillMigrationQuarantineModel.entity_id == scope.entity_id,
                    SkillMigrationQuarantineModel.bot_id == scope.bot_id,
                    SkillMigrationQuarantineModel.migration_generation
                    == migration_generation,
                    SkillMigrationQuarantineModel.cleaned_at.is_(None),
                    SkillMigrationQuarantineModel.pool_activated_at
                    <= eligible_before_db,
                    SkillMigrationQuarantineModel.runtime_reconciled_at
                    > SkillMigrationQuarantineModel.pool_activated_at,
                    SkillMigrationQuarantineModel.runtime_reconciliation_status
                    == RuntimeReconciliationStatus.READY.value,
                    or_(
                        SkillMigrationQuarantineModel.status.in_(
                            ("retained", "cleanup_failed")
                        ),
                        SkillMigrationQuarantineModel.cleanup_lease_expires_at
                        <= func.now(),
                    ),
                )
                .update(
                    {
                        SkillMigrationQuarantineModel.status: "cleaning",
                        SkillMigrationQuarantineModel.cleanup_lease_owner: (
                            cleanup_owner
                        ),
                        SkillMigrationQuarantineModel.cleanup_lease_expires_at: (
                            self._cleanup_lease_deadline(session, lease_seconds)
                        ),
                    },
                    synchronize_session=False,
                )
            )
        return affected == 1

    @staticmethod
    def _holds_cleanup_fence(
        session,
        *,
        scope: BotSkillLayoutScope,
        migration_generation: str,
    ) -> bool:
        return (
            session.query(BotSkillLayoutStateModel.id)
            .filter(
                BotSkillLayoutStateModel.env == scope.env,
                BotSkillLayoutStateModel.entity_id == scope.entity_id,
                BotSkillLayoutStateModel.bot_id == scope.bot_id,
                BotSkillLayoutStateModel.active_layout == SkillLayout.POOL.value,
                BotSkillLayoutStateModel.phase == SkillLayoutPhase.POOL_ACTIVE.value,
                BotSkillLayoutStateModel.migration_generation == migration_generation,
            )
            .with_for_update()
            .first()
            is not None
        )

    def mark_cleaned(
        self,
        *,
        scope: BotSkillLayoutScope,
        migration_generation: str,
        cleanup_owner: str,
        evidence: dict[str, object],
    ) -> bool:
        with self._database.transactional_orm_session() as session:
            if not self._holds_cleanup_fence(
                session,
                scope=scope,
                migration_generation=migration_generation,
            ):
                return False
            query = session.query(SkillMigrationQuarantineModel).filter(
                SkillMigrationQuarantineModel.env == scope.env,
                SkillMigrationQuarantineModel.entity_id == scope.entity_id,
                SkillMigrationQuarantineModel.bot_id == scope.bot_id,
                SkillMigrationQuarantineModel.migration_generation
                == migration_generation,
                SkillMigrationQuarantineModel.status == "cleaning",
                SkillMigrationQuarantineModel.cleanup_lease_owner == cleanup_owner,
                SkillMigrationQuarantineModel.cleanup_lease_expires_at > func.now(),
            )
            affected = query.filter(
                SkillMigrationQuarantineModel.cleaned_at.is_(None)
            ).update(
                {
                    SkillMigrationQuarantineModel.status: "cleaned",
                    SkillMigrationQuarantineModel.cleaned_at: func.now(),
                    SkillMigrationQuarantineModel.cleanup_evidence: json.dumps(
                        evidence,
                        ensure_ascii=False,
                    ),
                    SkillMigrationQuarantineModel.cleanup_lease_owner: None,
                    SkillMigrationQuarantineModel.cleanup_lease_expires_at: None,
                },
                synchronize_session=False,
            )
            if affected == 1:
                return True
            return False

    def mark_cleanup_failed(
        self,
        *,
        scope: BotSkillLayoutScope,
        migration_generation: str,
        cleanup_owner: str,
        evidence: dict[str, object],
    ) -> bool:
        with self._database.transactional_orm_session() as session:
            if not self._holds_cleanup_fence(
                session,
                scope=scope,
                migration_generation=migration_generation,
            ):
                return False
            affected = (
                session.query(SkillMigrationQuarantineModel)
                .filter(
                    SkillMigrationQuarantineModel.env == scope.env,
                    SkillMigrationQuarantineModel.entity_id == scope.entity_id,
                    SkillMigrationQuarantineModel.bot_id == scope.bot_id,
                    SkillMigrationQuarantineModel.migration_generation
                    == migration_generation,
                    SkillMigrationQuarantineModel.status == "cleaning",
                    SkillMigrationQuarantineModel.cleanup_lease_owner == cleanup_owner,
                    SkillMigrationQuarantineModel.cleanup_lease_expires_at > func.now(),
                )
                .update(
                    {
                        SkillMigrationQuarantineModel.status: "cleanup_failed",
                        SkillMigrationQuarantineModel.cleanup_evidence: json.dumps(
                            evidence,
                            ensure_ascii=False,
                        ),
                        SkillMigrationQuarantineModel.cleanup_lease_owner: None,
                        SkillMigrationQuarantineModel.cleanup_lease_expires_at: None,
                    },
                    synchronize_session=False,
                )
            )
        return affected == 1

    def record_cleanup_uncertain(
        self,
        *,
        scope: BotSkillLayoutScope,
        migration_generation: str,
        cleanup_owner: str,
        evidence: dict[str, object],
    ) -> bool:
        """Audit an unknown runtime outcome without releasing its delete fence."""
        with self._database.transactional_orm_session() as session:
            if not self._holds_cleanup_fence(
                session,
                scope=scope,
                migration_generation=migration_generation,
            ):
                return False
            affected = (
                session.query(SkillMigrationQuarantineModel)
                .filter(
                    SkillMigrationQuarantineModel.env == scope.env,
                    SkillMigrationQuarantineModel.entity_id == scope.entity_id,
                    SkillMigrationQuarantineModel.bot_id == scope.bot_id,
                    SkillMigrationQuarantineModel.migration_generation
                    == migration_generation,
                    SkillMigrationQuarantineModel.status == "cleaning",
                    SkillMigrationQuarantineModel.cleanup_lease_owner == cleanup_owner,
                    SkillMigrationQuarantineModel.cleanup_lease_expires_at > func.now(),
                )
                .update(
                    {
                        SkillMigrationQuarantineModel.cleanup_evidence: json.dumps(
                            evidence,
                            ensure_ascii=False,
                        ),
                    },
                    synchronize_session=False,
                )
            )
        return affected == 1
