"""Skills Pool 布局状态仓库的行为契约测试。"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
import logging
from pathlib import Path
from threading import Barrier

from sqlalchemy import create_engine, func, select, text
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy.schema import CreateTable

from agentclaw.community.core.base import Base
from agentclaw.community.core.skills_pool.quarantine import (
    QuarantineStatus,
    RuntimeReconciliationStatus,
)
from agentclaw.community.core.skills_pool.types import (
    BotSkillLayoutScope,
    RolloutEvidence,
    SkillLayout,
    SkillLayoutPhase,
)
from agentclaw.community.core.skills_pool.repository.models import (
    BotSkillLayoutStateModel,
    SkillMigrationQuarantineModel,
    SkillsPoolRolloutAuditModel,
)
from agentclaw.community.core.skills_pool.repository.protocol import (
    SkillsPoolLayoutRepositoryProtocol,
)
from agentclaw.community.core.models.skill import Skill
from agentclaw.community.plugins.skills_pool_layout_repository import (
    SkillsPoolLayoutRepository,
)
from agentclaw.community.plugins.skills_pool_quarantine_repository import (
    _database_timestamp,
)


class InMemorySqliteDB:
    """提供与生产一致的事务 ORM seam。"""

    def __init__(self) -> None:
        engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(engine)
        self._session_factory = sessionmaker(bind=engine, autoflush=False)

    @contextmanager
    def transactional_orm_session(self):
        session = self._session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()


class FileSqliteDB(InMemorySqliteDB):
    """为真实多连接竞争测试提供共享 SQLite 文件。"""

    def __init__(self, path: Path) -> None:
        engine = create_engine(
            f"sqlite:///{path}",
            connect_args={"check_same_thread": False, "timeout": 10},
        )
        Base.metadata.create_all(engine)
        self._session_factory = sessionmaker(bind=engine, autoflush=False)


def test_skills_pool_operational_tables_use_mysql_timestamp_contract() -> None:
    quarantine_ddl = str(
        CreateTable(SkillMigrationQuarantineModel.__table__).compile(
            dialect=mysql.dialect()
        )
    ).upper()
    audit_ddl = str(
        CreateTable(SkillsPoolRolloutAuditModel.__table__).compile(
            dialect=mysql.dialect()
        )
    ).upper()

    assert "DATETIME" not in quarantine_ddl
    assert "POOL_ACTIVATED_AT TIMESTAMP" in quarantine_ddl
    assert "RUNTIME_RECONCILED_AT TIMESTAMP(6)" in quarantine_ddl
    assert "CLEANED_AT TIMESTAMP" in quarantine_ddl
    assert "CLEANUP_LEASE_EXPIRES_AT TIMESTAMP" in quarantine_ddl
    assert "GMT_CREATE TIMESTAMP" in quarantine_ddl
    assert "GMT_MODIFIED TIMESTAMP" in quarantine_ddl

    assert "DATETIME" not in audit_ddl
    assert "EFFECTIVE_AT TIMESTAMP(6)" in audit_ddl
    assert "GMT_CREATE TIMESTAMP" in audit_ddl
    assert "GMT_MODIFY TIMESTAMP" in audit_ddl


def test_aware_runtime_observation_uses_mysql_session_timestamp() -> None:
    observed_at = datetime(2026, 7, 30, 12, 58, 13, 841734, tzinfo=UTC)

    expression = _database_timestamp(observed_at, dialect_name="mysql")
    compiled = str(
        select(expression).compile(
            dialect=mysql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    ).lower()

    assert "from_unixtime(1785416293.841734)" in compiled
    assert _database_timestamp(observed_at, dialect_name="sqlite") == (
        observed_at.replace(tzinfo=None)
    )


def rollout_evidence() -> RolloutEvidence:
    return RolloutEvidence(
        env="pre",
        config_id=42,
        config_version="2026-07-23T12:00:00",
        batch_id="openclaw-canary-1",
        engine_type="openclaw",
        decision_reason="exact_bot_match",
    )


def test_layout_repository_satisfies_public_protocol_shape() -> None:
    repository = SkillsPoolLayoutRepository(InMemorySqliteDB())

    assert isinstance(repository, SkillsPoolLayoutRepositoryProtocol)


def test_cutover_commit_logs_missing_quarantine_path(caplog) -> None:
    database = InMemorySqliteDB()
    repository = SkillsPoolLayoutRepository(database)
    scope = BotSkillLayoutScope(env="pre", entity_id="entity-1", bot_id="bot-1")
    repository.claim_pool_migration(
        scope=scope,
        layout_contract_version="skills-pool-p3-v1",
        migration_generation="generation-1",
        rollout_evidence=rollout_evidence(),
        lease_owner="worker-1",
        lease_seconds=60,
    )
    assert repository.record_ready_probe(
        scope=scope,
        migration_generation="generation-1",
        lease_owner="worker-1",
        preparation_id="preparation-1",
        evidence={"marker": "valid"},
    )
    assert repository.begin_cutover(
        scope=scope,
        migration_generation="generation-1",
        lease_owner="worker-1",
        preparation_id="preparation-1",
    )

    with caplog.at_level(logging.ERROR, logger="start"):
        committed = repository.record_cutover_committed(
            scope=scope,
            migration_generation="generation-1",
            lease_owner="worker-1",
            preparation_id="preparation-1",
            evidence={
                "committed": True,
                "status": "COMMITTED",
                "evidence": {"bridge": "valid"},
            },
        )

    assert committed is False
    assert (
        "reason=missing_quarantine_path env=pre entity_id=entity-1 "
        "bot_id=bot-1 migration_generation=generation-1"
    ) in caplog.text


def test_missing_layout_state_reads_as_legacy_without_persisting() -> None:
    database = InMemorySqliteDB()
    repository = SkillsPoolLayoutRepository(database)
    scope = BotSkillLayoutScope(env="pre", entity_id="entity-1", bot_id="bot-1")

    state = repository.get(scope)

    assert state.scope == scope
    assert state.active_layout is SkillLayout.LEGACY
    assert state.target_layout is None
    assert state.phase is SkillLayoutPhase.LEGACY_ACTIVE
    assert state.migration_generation is None
    assert state.persisted is False


def test_list_states_is_scoped_to_environment_and_only_returns_persisted_rows() -> None:
    database = InMemorySqliteDB()
    repository = SkillsPoolLayoutRepository(database)
    with database.transactional_orm_session() as session:
        session.add_all(
            [
                BotSkillLayoutStateModel(
                    env="pre",
                    entity_id="entity-2",
                    bot_id="bot-2",
                    active_layout=SkillLayout.LEGACY.value,
                    phase=SkillLayoutPhase.LEGACY_ACTIVE.value,
                ),
                BotSkillLayoutStateModel(
                    env="pre",
                    entity_id="entity-1",
                    bot_id="bot-1",
                    active_layout=SkillLayout.LEGACY.value,
                    phase=SkillLayoutPhase.LEGACY_ACTIVE.value,
                ),
                BotSkillLayoutStateModel(
                    env="prod",
                    entity_id="entity-3",
                    bot_id="bot-3",
                    active_layout=SkillLayout.LEGACY.value,
                    phase=SkillLayoutPhase.LEGACY_ACTIVE.value,
                ),
            ]
        )

    states = repository.list_states(env="pre")

    assert [state.scope.bot_id for state in states] == ["bot-2", "bot-1"]
    assert all(state.persisted for state in states)

    with database.transactional_orm_session() as session:
        rows = {
            row.bot_id: row
            for row in session.query(BotSkillLayoutStateModel)
            .filter(BotSkillLayoutStateModel.env == "pre")
            .all()
        }
        rows["bot-1"].rollout_evidence = json.dumps(
            {
                "env": "pre",
                "config_id": 1,
                "config_version": "v1",
                "batch_id": "batch-1",
                "engine_type": "openclaw",
                "decision_reason": "eligible",
            }
        )
        rows["bot-2"].rollout_evidence = json.dumps(
            {
                "env": "pre",
                "config_id": 1,
                "config_version": "v1",
                "batch_id": "batch-1",
                "engine_type": "claude_code",
                "decision_reason": "eligible",
            }
        )

    filtered = repository.list_states(
        env="pre",
        engine="openclaw",
        batch_id="batch-1",
    )

    assert [state.scope.bot_id for state in filtered] == ["bot-1"]


def test_claim_pool_migration_persists_generation_lease_and_rollout_evidence() -> None:
    database = InMemorySqliteDB()
    repository = SkillsPoolLayoutRepository(database)
    scope = BotSkillLayoutScope(env="pre", entity_id="entity-1", bot_id="bot-1")
    evidence = rollout_evidence()

    claimed = repository.claim_pool_migration(
        scope=scope,
        layout_contract_version="skills-pool-v1",
        migration_generation="generation-1",
        rollout_evidence=evidence,
        lease_owner="worker-1",
        lease_seconds=60,
    )

    assert claimed is not None
    assert claimed.active_layout is SkillLayout.LEGACY
    assert claimed.target_layout is SkillLayout.POOL
    assert claimed.phase is SkillLayoutPhase.POOL_PREPARING
    assert claimed.layout_contract_version == "skills-pool-v1"
    assert claimed.migration_generation == "generation-1"
    assert claimed.rollout_evidence == evidence
    assert claimed.data_plane_cutover_committed is False
    assert claimed.lease_owner == "worker-1"
    assert claimed.lease_expires_at is not None
    assert claimed.persisted is True


def test_not_capable_probe_releases_preparing_claim_and_preserves_evidence() -> None:
    repository = SkillsPoolLayoutRepository(InMemorySqliteDB())
    scope = BotSkillLayoutScope(env="pre", entity_id="entity-1", bot_id="bot-1")
    repository.claim_pool_migration(
        scope=scope,
        layout_contract_version="skills-pool-v1",
        migration_generation="generation-1",
        rollout_evidence=rollout_evidence(),
        lease_owner="worker-1",
        lease_seconds=60,
    )

    released = repository.release_not_capable_claim(
        scope=scope,
        migration_generation="generation-1",
        lease_owner="worker-1",
        evidence={"reason": "pool_marker_missing"},
    )

    assert released is True
    state = repository.get(scope)
    assert state.active_layout is SkillLayout.LEGACY
    assert state.target_layout is None
    assert state.phase is SkillLayoutPhase.LEGACY_ACTIVE
    assert state.migration_generation is None
    assert state.lease_owner is None
    assert state.lease_expires_at is None
    assert state.last_probe_result == "NOT_CAPABLE"
    assert state.last_probe_evidence == {"reason": "pool_marker_missing"}
    assert state.data_plane_cutover_committed is False


def test_not_capable_probe_releases_ready_claim_before_cutover() -> None:
    repository = SkillsPoolLayoutRepository(InMemorySqliteDB())
    scope = BotSkillLayoutScope(env="pre", entity_id="entity-1", bot_id="bot-1")
    repository.claim_pool_migration(
        scope=scope,
        layout_contract_version="skills-pool-v1",
        migration_generation="generation-1",
        rollout_evidence=rollout_evidence(),
        lease_owner="worker-1",
        lease_seconds=60,
    )
    assert repository.record_ready_probe(
        scope=scope,
        migration_generation="generation-1",
        lease_owner="worker-1",
        preparation_id="preparation-1",
        evidence={"marker": "valid"},
    )

    assert repository.release_not_capable_claim(
        scope=scope,
        migration_generation="generation-1",
        lease_owner="worker-1",
        evidence={"reason": "runtime_contract_missing"},
    )

    state = repository.get(scope)
    assert state.phase is SkillLayoutPhase.LEGACY_ACTIVE
    assert state.target_layout is None
    assert state.migration_generation is None
    assert state.preparation_id is None


def test_changed_engine_releases_ready_claim_before_cutover() -> None:
    repository = SkillsPoolLayoutRepository(InMemorySqliteDB())
    scope = BotSkillLayoutScope(env="pre", entity_id="entity-1", bot_id="bot-1")
    repository.claim_pool_migration(
        scope=scope,
        layout_contract_version="skills-pool-v1",
        migration_generation="generation-1",
        rollout_evidence=rollout_evidence(),
        lease_owner="worker-1",
        lease_seconds=60,
    )
    assert repository.record_ready_probe(
        scope=scope,
        migration_generation="generation-1",
        lease_owner="worker-1",
        preparation_id="preparation-1",
        evidence={"marker": "valid"},
    )

    assert repository.release_changed_engine_claim(
        scope=scope,
        migration_generation="generation-1",
        lease_owner="worker-1",
        evidence={
            "reason": "bot_engine_changed",
            "claimed_engine": "openclaw",
            "current_engine": "claude_code",
        },
    )

    state = repository.get(scope)
    assert state.phase is SkillLayoutPhase.LEGACY_ACTIVE
    assert state.target_layout is None
    assert state.migration_generation is None
    assert state.last_probe_result == "BOT_CHANGED"
    assert state.last_probe_evidence == {
        "reason": "bot_engine_changed",
        "claimed_engine": "openclaw",
        "current_engine": "claude_code",
    }


def test_claim_updates_an_existing_unclaimed_legacy_row() -> None:
    database = InMemorySqliteDB()
    repository = SkillsPoolLayoutRepository(database)
    scope = BotSkillLayoutScope(env="pre", entity_id="entity-1", bot_id="bot-1")
    with database.transactional_orm_session() as session:
        session.add(
            BotSkillLayoutStateModel(
                env=scope.env,
                entity_id=scope.entity_id,
                bot_id=scope.bot_id,
                active_layout=SkillLayout.LEGACY.value,
                phase=SkillLayoutPhase.LEGACY_ACTIVE.value,
            )
        )

    claimed = repository.claim_pool_migration(
        scope=scope,
        layout_contract_version="skills-pool-v1",
        migration_generation="generation-1",
        rollout_evidence=rollout_evidence(),
        lease_owner="worker-1",
        lease_seconds=60,
    )

    assert claimed is not None
    assert claimed.migration_generation == "generation-1"
    with database.transactional_orm_session() as session:
        assert session.query(BotSkillLayoutStateModel).count() == 1


def test_repeated_claim_keeps_the_first_migration_generation() -> None:
    repository = SkillsPoolLayoutRepository(InMemorySqliteDB())
    scope = BotSkillLayoutScope(env="pre", entity_id="entity-1", bot_id="bot-1")

    first = repository.claim_pool_migration(
        scope=scope,
        layout_contract_version="skills-pool-v1",
        migration_generation="generation-1",
        rollout_evidence=rollout_evidence(),
        lease_owner="worker-1",
        lease_seconds=60,
    )
    repeated = repository.claim_pool_migration(
        scope=scope,
        layout_contract_version="skills-pool-v1",
        migration_generation="generation-2",
        rollout_evidence=rollout_evidence(),
        lease_owner="worker-2",
        lease_seconds=60,
    )

    assert first is not None
    assert repeated is None
    assert repository.get(scope).migration_generation == "generation-1"


def test_concurrent_claim_has_exactly_one_generation(tmp_path: Path) -> None:
    database = FileSqliteDB(tmp_path / "layout-state.db")
    scope = BotSkillLayoutScope(env="pre", entity_id="entity-1", bot_id="bot-1")
    barrier = Barrier(2)

    def claim(generation: str):
        repository = SkillsPoolLayoutRepository(database)
        barrier.wait()
        return repository.claim_pool_migration(
            scope=scope,
            layout_contract_version="skills-pool-v1",
            migration_generation=generation,
            rollout_evidence=rollout_evidence(),
            lease_owner=generation,
            lease_seconds=60,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(claim, ["generation-1", "generation-2"]))

    winners = [state for state in results if state is not None]
    assert len(winners) == 1
    assert (
        SkillsPoolLayoutRepository(database).get(scope).migration_generation
        == winners[0].migration_generation
    )


def test_lease_is_fenced_by_generation_and_current_owner() -> None:
    repository = SkillsPoolLayoutRepository(InMemorySqliteDB())
    scope = BotSkillLayoutScope(env="pre", entity_id="entity-1", bot_id="bot-1")
    repository.claim_pool_migration(
        scope=scope,
        layout_contract_version="skills-pool-v1",
        migration_generation="generation-1",
        rollout_evidence=rollout_evidence(),
        lease_owner="worker-1",
        lease_seconds=60,
    )

    assert repository.renew_lease(
        scope=scope,
        migration_generation="generation-1",
        lease_owner="worker-1",
        lease_seconds=120,
    )
    assert not repository.renew_lease(
        scope=scope,
        migration_generation="generation-stale",
        lease_owner="worker-1",
        lease_seconds=120,
    )
    assert not repository.renew_lease(
        scope=scope,
        migration_generation="generation-1",
        lease_owner="worker-2",
        lease_seconds=120,
    )
    assert repository.holds_lease(
        scope=scope,
        migration_generation="generation-1",
        lease_owner="worker-1",
    )
    assert not repository.holds_lease(
        scope=scope,
        migration_generation="generation-stale",
        lease_owner="worker-1",
    )


def test_expired_lease_competition_has_one_new_owner(tmp_path: Path) -> None:
    database = FileSqliteDB(tmp_path / "layout-lease.db")
    scope = BotSkillLayoutScope(env="pre", entity_id="entity-1", bot_id="bot-1")
    repository = SkillsPoolLayoutRepository(database)
    repository.claim_pool_migration(
        scope=scope,
        layout_contract_version="skills-pool-v1",
        migration_generation="generation-1",
        rollout_evidence=rollout_evidence(),
        lease_owner="worker-original",
        lease_seconds=60,
    )
    with database.transactional_orm_session() as session:
        session.query(BotSkillLayoutStateModel).update(
            {
                BotSkillLayoutStateModel.lease_expires_at: func.datetime(
                    func.now(), text("'-1 second'")
                )
            }
        )
    assert not repository.holds_lease(
        scope=scope,
        migration_generation="generation-1",
        lease_owner="worker-original",
    )

    barrier = Barrier(2)

    def acquire(worker: str) -> bool:
        barrier.wait()
        return SkillsPoolLayoutRepository(database).try_acquire_lease(
            scope=scope,
            migration_generation="generation-1",
            lease_owner=worker,
            lease_seconds=60,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(acquire, ["worker-2", "worker-3"]))

    assert results.count(True) == 1
    winner = ["worker-2", "worker-3"][results.index(True)]
    assert repository.get(scope).lease_owner == winner


def test_pre_cutover_failure_evidence_survives_an_ordinary_retry() -> None:
    repository = SkillsPoolLayoutRepository(InMemorySqliteDB())
    scope = BotSkillLayoutScope(env="pre", entity_id="entity-1", bot_id="bot-1")
    repository.claim_pool_migration(
        scope=scope,
        layout_contract_version="skills-pool-p3-v1",
        migration_generation="generation-1",
        rollout_evidence=rollout_evidence(),
        lease_owner="worker-1",
        lease_seconds=60,
    )
    assert repository.record_ready_probe(
        scope=scope,
        migration_generation="generation-1",
        lease_owner="worker-1",
        preparation_id="preparation-1",
        evidence={"marker": "valid"},
    )
    assert repository.begin_cutover(
        scope=scope,
        migration_generation="generation-1",
        lease_owner="worker-1",
        preparation_id="preparation-1",
    )
    failure_evidence = {
        "status": "DATA_INCONSISTENT",
        "evidence": {
            "reason": "registered_local_source_missing",
            "registered_name": "handmade",
        },
    }

    assert repository.record_pre_cutover_failure(
        scope=scope,
        migration_generation="generation-1",
        lease_owner="worker-1",
        failure_code="DATA_INCONSISTENT",
        failure_stage="pre_cutover_validation",
        retryable=False,
        evidence=failure_evidence,
    )
    assert repository.record_ready_probe(
        scope=scope,
        migration_generation="generation-1",
        lease_owner="worker-1",
        preparation_id="preparation-1",
        evidence={"marker": "still-valid"},
    )

    state = repository.get(scope)
    assert state.phase is SkillLayoutPhase.POOL_READY
    assert state.last_probe_evidence == {"marker": "still-valid"}
    assert state.last_failure_code == "DATA_INCONSISTENT"
    assert state.last_failure_stage == "pre_cutover_validation"
    assert state.last_failure_retryable is False
    assert state.last_failure_evidence == failure_evidence
    assert state.last_failure_at is not None
    assert not repository.record_pre_cutover_failure(
        scope=scope,
        migration_generation="stale-generation",
        lease_owner="worker-1",
        failure_code="ACTIVE_ENTRY_CONFLICT",
        failure_stage="pre_cutover_validation",
        retryable=False,
        evidence={
            "committed": True,
            "status": "COMMITTED",
            "evidence": {
                "quarantine": (
                    "/home/admin/.openclaw/workspace/skills-pool/"
                    ".migration-quarantine/generation-1/skills-local"
                )
            },
        },
    )


def test_invalid_runtime_probe_can_be_recorded_while_pool_is_preparing() -> None:
    repository = SkillsPoolLayoutRepository(InMemorySqliteDB())
    scope = BotSkillLayoutScope(env="pre", entity_id="entity-1", bot_id="bot-1")
    repository.claim_pool_migration(
        scope=scope,
        layout_contract_version="skills-pool-p3-v1",
        migration_generation="generation-1",
        rollout_evidence=rollout_evidence(),
        lease_owner="worker-1",
        lease_seconds=60,
    )

    recorded = repository.record_pre_cutover_failure(
        scope=scope,
        migration_generation="generation-1",
        lease_owner="worker-1",
        failure_code="INVALID",
        failure_stage="runtime_probe",
        retryable=False,
        evidence={"reason": "marker_contract_mismatch"},
    )

    assert recorded
    state = repository.get(scope)
    assert state.phase is SkillLayoutPhase.POOL_PREPARING
    assert state.last_failure_code == "INVALID"
    assert state.last_failure_stage == "runtime_probe"
    assert state.last_failure_retryable is False
    assert state.last_failure_evidence == {"reason": "marker_contract_mismatch"}


def test_post_cutover_failure_remains_forward_only_and_auditable() -> None:
    repository = SkillsPoolLayoutRepository(InMemorySqliteDB())
    scope = BotSkillLayoutScope(env="pre", entity_id="entity-1", bot_id="bot-1")
    repository.claim_pool_migration(
        scope=scope,
        layout_contract_version="skills-pool-p3-v1",
        migration_generation="generation-1",
        rollout_evidence=rollout_evidence(),
        lease_owner="worker-1",
        lease_seconds=60,
    )
    assert repository.record_ready_probe(
        scope=scope,
        migration_generation="generation-1",
        lease_owner="worker-1",
        preparation_id="preparation-1",
        evidence={"marker": "valid"},
    )
    assert repository.begin_cutover(
        scope=scope,
        migration_generation="generation-1",
        lease_owner="worker-1",
        preparation_id="preparation-1",
    )
    assert not repository.record_cutover_committed(
        scope=scope,
        migration_generation="generation-1",
        lease_owner="worker-1",
        preparation_id="preparation-1",
        evidence={
            "committed": True,
            "status": "COMMITTED",
            "evidence": {"bridge": "valid"},
        },
    )
    assert repository.get(scope).phase is SkillLayoutPhase.POOL_ACTIVATING_PRE_CUTOVER
    assert repository.record_cutover_committed(
        scope=scope,
        migration_generation="generation-1",
        lease_owner="worker-1",
        preparation_id="preparation-1",
        evidence={
            "committed": True,
            "status": "COMMITTED",
            "evidence": {
                "bridge": "pool",
                "quarantine": (
                    "/home/admin/.openclaw/workspace/skills-pool/"
                    ".migration-quarantine/generation-1/skills-local"
                ),
            },
        },
    )

    assert repository.record_post_cutover_failure(
        scope=scope,
        migration_generation="generation-1",
        lease_owner="worker-1",
        failure_code="MAPPING_PUBLISH_FAILED",
        failure_stage="mapping_publish",
        retryable=True,
        evidence={"mapping_count": 3},
    )

    state = repository.get(scope)
    assert state.phase is SkillLayoutPhase.POOL_CUTOVER_COMMITTED
    assert state.data_plane_cutover_committed is True
    assert state.last_failure_code == "MAPPING_PUBLISH_FAILED"
    assert state.last_failure_stage == "mapping_publish"
    assert state.last_failure_retryable is True
    assert state.last_failure_evidence == {"mapping_count": 3}
    assert state.last_failure_at is not None


def test_unknown_cutover_is_fenced_for_manual_repair() -> None:
    repository = SkillsPoolLayoutRepository(InMemorySqliteDB())
    scope = BotSkillLayoutScope(env="pre", entity_id="entity-1", bot_id="bot-1")
    repository.claim_pool_migration(
        scope=scope,
        layout_contract_version="skills-pool-p3-v1",
        migration_generation="generation-1",
        rollout_evidence=rollout_evidence(),
        lease_owner="worker-1",
        lease_seconds=60,
    )
    assert repository.record_ready_probe(
        scope=scope,
        migration_generation="generation-1",
        lease_owner="worker-1",
        preparation_id="preparation-1",
        evidence={"marker": "valid"},
    )
    assert repository.begin_cutover(
        scope=scope,
        migration_generation="generation-1",
        lease_owner="worker-1",
        preparation_id="preparation-1",
    )

    assert repository.mark_repair_required(
        scope=scope,
        migration_generation="generation-1",
        lease_owner="worker-1",
        failure_code="UNKNOWN",
        failure_stage="cutover_outcome_unknown",
        evidence={"reason": "response_lost"},
    )

    state = repository.get(scope)
    assert state.phase is SkillLayoutPhase.NEEDS_MANUAL_REPAIR
    assert state.data_plane_cutover_committed is False
    assert state.last_failure_retryable is False
    assert state.lease_owner is None
    assert state.last_failure_evidence == {"reason": "response_lost"}


def test_cutover_finalizing_persists_quarantine_at_irreversible_boundary() -> None:
    database = InMemorySqliteDB()
    repository = SkillsPoolLayoutRepository(database)
    scope = BotSkillLayoutScope(env="pre", entity_id="entity-1", bot_id="bot-1")
    repository.claim_pool_migration(
        scope=scope,
        layout_contract_version="skills-pool-p3-v1",
        migration_generation="generation-1",
        rollout_evidence=rollout_evidence(),
        lease_owner="worker-1",
        lease_seconds=60,
    )
    assert repository.record_ready_probe(
        scope=scope,
        migration_generation="generation-1",
        lease_owner="worker-1",
        preparation_id="preparation-1",
        evidence={"marker": "valid"},
    )
    assert repository.begin_cutover(
        scope=scope,
        migration_generation="generation-1",
        lease_owner="worker-1",
        preparation_id="preparation-1",
    )
    quarantine_path = (
        "/home/admin/.openclaw/workspace/skills-pool/"
        ".migration-quarantine/generation-1/skills-local"
    )

    assert repository.record_cutover_finalizing(
        scope=scope,
        migration_generation="generation-1",
        lease_owner="worker-1",
        preparation_id="preparation-1",
        evidence={
            "committed": False,
            "status": "POST_CUTOVER_SYNC_PENDING",
            "evidence": {"quarantine": quarantine_path},
        },
    )

    state = repository.get(scope)
    assert state.phase is SkillLayoutPhase.POOL_CUTOVER_FINALIZING
    assert state.data_plane_cutover_committed is True
    with database.transactional_orm_session() as session:
        quarantine = session.query(SkillMigrationQuarantineModel).one()
        assert quarantine.path == quarantine_path
        assert quarantine.pool_activated_at is None


def test_cutover_finalizing_accepts_pending_evidence_without_quarantine() -> None:
    database = InMemorySqliteDB()
    repository = SkillsPoolLayoutRepository(database)
    scope = BotSkillLayoutScope(env="pre", entity_id="entity-1", bot_id="bot-1")
    repository.claim_pool_migration(
        scope=scope,
        layout_contract_version="skills-pool-p3-v1",
        migration_generation="generation-1",
        rollout_evidence=rollout_evidence(),
        lease_owner="worker-1",
        lease_seconds=60,
    )
    assert repository.record_ready_probe(
        scope=scope,
        migration_generation="generation-1",
        lease_owner="worker-1",
        preparation_id="preparation-1",
        evidence={"marker": "valid"},
    )
    assert repository.begin_cutover(
        scope=scope,
        migration_generation="generation-1",
        lease_owner="worker-1",
        preparation_id="preparation-1",
    )

    assert repository.record_cutover_finalizing(
        scope=scope,
        migration_generation="generation-1",
        lease_owner="worker-1",
        preparation_id="preparation-1",
        evidence={
            "committed": False,
            "status": "POST_CUTOVER_SYNC_PENDING",
            "evidence": {"reason": "post_cutover_sync_failed"},
        },
    )

    state = repository.get(scope)
    assert state.phase is SkillLayoutPhase.POOL_CUTOVER_FINALIZING
    assert state.data_plane_cutover_committed is True
    with database.transactional_orm_session() as session:
        assert session.query(SkillMigrationQuarantineModel).count() == 0


def test_finalizing_without_quarantine_accepts_runtime_identity_and_commits() -> None:
    database = InMemorySqliteDB()
    repository = SkillsPoolLayoutRepository(database)
    scope = BotSkillLayoutScope(env="pre", entity_id="entity-1", bot_id="bot-1")
    repository.claim_pool_migration(
        scope=scope,
        layout_contract_version="skills-pool-p3-v1",
        migration_generation="generation-1",
        rollout_evidence=rollout_evidence(),
        lease_owner="worker-1",
        lease_seconds=60,
    )
    assert repository.record_ready_probe(
        scope=scope,
        migration_generation="generation-1",
        lease_owner="worker-1",
        preparation_id="preparation-1",
        evidence={"marker": "valid"},
    )
    assert repository.begin_cutover(
        scope=scope,
        migration_generation="generation-1",
        lease_owner="worker-1",
        preparation_id="preparation-1",
    )
    assert repository.record_cutover_finalizing(
        scope=scope,
        migration_generation="generation-1",
        lease_owner="worker-1",
        preparation_id="preparation-1",
        evidence={
            "committed": False,
            "status": "POST_CUTOVER_SYNC_PENDING",
            "evidence": {"reason": "transport_response_unavailable"},
        },
    )
    with database.transactional_orm_session() as session:
        assert session.query(SkillMigrationQuarantineModel).count() == 0

    quarantine_path = (
        "/home/admin/.aicoding/workspace/skills-pool/"
        ".migration-quarantine/generation-1/skills-local"
    )
    assert repository.record_post_cutover_evidence(
        scope=scope,
        migration_generation="generation-1",
        lease_owner="worker-1",
        preparation_id="preparation-1",
        evidence={
            "committed": True,
            "status": "ALREADY_COMMITTED",
            "evidence": {
                "active_marker": "same-generation",
                "quarantine": quarantine_path,
                "quarantine_cleanup_pending": True,
            },
        },
    )
    assert repository.has_quarantine_identity(
        scope=scope,
        migration_generation="generation-1",
    )
    assert repository.commit_pool_active(
        scope=scope,
        migration_generation="generation-1",
        lease_owner="worker-1",
        preparation_id="preparation-1",
        local_locators={},
    )

    state = repository.get(scope)
    assert state.active_layout is SkillLayout.POOL
    assert state.phase is SkillLayoutPhase.POOL_ACTIVE


def test_post_cutover_evidence_reuses_existing_quarantine_identity() -> None:
    database = InMemorySqliteDB()
    repository = SkillsPoolLayoutRepository(database)
    scope = BotSkillLayoutScope(env="pre", entity_id="entity-1", bot_id="bot-1")
    repository.claim_pool_migration(
        scope=scope,
        layout_contract_version="skills-pool-p3-v1",
        migration_generation="generation-1",
        rollout_evidence=rollout_evidence(),
        lease_owner="worker-1",
        lease_seconds=60,
    )
    assert repository.record_ready_probe(
        scope=scope,
        migration_generation="generation-1",
        lease_owner="worker-1",
        preparation_id="preparation-1",
        evidence={"marker": "valid"},
    )
    assert repository.begin_cutover(
        scope=scope,
        migration_generation="generation-1",
        lease_owner="worker-1",
        preparation_id="preparation-1",
    )
    quarantine_path = (
        "/home/admin/.openclaw/workspace/skills-pool/"
        ".migration-quarantine/generation-1/skills-local"
    )
    assert repository.record_cutover_committed(
        scope=scope,
        migration_generation="generation-1",
        lease_owner="worker-1",
        preparation_id="preparation-1",
        evidence={
            "committed": True,
            "status": "COMMITTED",
            "evidence": {"quarantine": quarantine_path},
        },
    )

    assert repository.has_quarantine_identity(
        scope=scope,
        migration_generation="generation-1",
    )
    assert not repository.quarantine_identity_conflicts(
        scope=scope,
        migration_generation="generation-1",
        engine="openclaw",
        path=quarantine_path,
    )
    assert repository.quarantine_identity_conflicts(
        scope=scope,
        migration_generation="generation-1",
        engine="openclaw",
        path=f"{quarantine_path}-other",
    )
    assert repository.quarantine_identity_conflicts(
        scope=scope,
        migration_generation="generation-1",
        engine="claude_code",
        path=quarantine_path,
    )
    assert repository.record_post_cutover_evidence(
        scope=scope,
        migration_generation="generation-1",
        lease_owner="worker-1",
        preparation_id="preparation-1",
        evidence={
            "committed": True,
            "status": "ALREADY_COMMITTED",
            "evidence": {"active_marker": "same-generation"},
        },
    )
    assert repository.record_post_cutover_evidence(
        scope=scope,
        migration_generation="generation-1",
        lease_owner="worker-1",
        preparation_id="preparation-1",
        evidence={
            "committed": True,
            "status": "ALREADY_COMMITTED",
            "evidence": {
                "active_marker": "same-generation",
                "quarantine": "",
            },
        },
    )

    state = repository.get(scope)
    assert state.phase is SkillLayoutPhase.POOL_CUTOVER_COMMITTED
    assert (
        state.last_probe_evidence["cutover"]["post_cutover_evidence_recorded"]
        is True
    )
    assert state.last_probe_evidence["cutover"]["evidence"] == {
        "active_marker": "same-generation",
        "quarantine": "",
    }
    with database.transactional_orm_session() as session:
        quarantine = session.query(SkillMigrationQuarantineModel).one()
        assert quarantine.path == quarantine_path


def test_cutover_commit_reuses_quarantine_from_pending_response() -> None:
    database = InMemorySqliteDB()
    repository = SkillsPoolLayoutRepository(database)
    scope = BotSkillLayoutScope(env="pre", entity_id="entity-1", bot_id="bot-1")
    repository.claim_pool_migration(
        scope=scope,
        layout_contract_version="skills-pool-p3-v1",
        migration_generation="generation-1",
        rollout_evidence=rollout_evidence(),
        lease_owner="worker-1",
        lease_seconds=60,
    )
    assert repository.record_ready_probe(
        scope=scope,
        migration_generation="generation-1",
        lease_owner="worker-1",
        preparation_id="preparation-1",
        evidence={"marker": "valid"},
    )
    assert repository.begin_cutover(
        scope=scope,
        migration_generation="generation-1",
        lease_owner="worker-1",
        preparation_id="preparation-1",
    )
    quarantine_path = (
        "/home/admin/.openclaw/workspace/skills-pool/"
        ".migration-quarantine/generation-1/skills-local"
    )
    assert repository.record_cutover_finalizing(
        scope=scope,
        migration_generation="generation-1",
        lease_owner="worker-1",
        preparation_id="preparation-1",
        evidence={
            "status": "POST_CUTOVER_SYNC_PENDING",
            "evidence": {"quarantine": quarantine_path},
        },
    )

    assert repository.record_cutover_committed(
        scope=scope,
        migration_generation="generation-1",
        lease_owner="worker-1",
        preparation_id="preparation-1",
        evidence={
            "committed": True,
            "status": "ALREADY_COMMITTED",
            "evidence": {"active_marker": "same-generation"},
        },
    )
    state = repository.get(scope)
    assert state.phase is SkillLayoutPhase.POOL_CUTOVER_COMMITTED
    assert state.data_plane_cutover_committed is True


def test_operator_resolves_manual_repair_with_note_and_explicit_fact() -> None:
    database = InMemorySqliteDB()
    repository = SkillsPoolLayoutRepository(database)
    scope = BotSkillLayoutScope(env="pre", entity_id="entity-1", bot_id="bot-1")
    repository.claim_pool_migration(
        scope=scope,
        layout_contract_version="skills-pool-p3-v1",
        migration_generation="generation-1",
        rollout_evidence=rollout_evidence(),
        lease_owner="worker-1",
        lease_seconds=60,
    )
    assert repository.record_ready_probe(
        scope=scope,
        migration_generation="generation-1",
        lease_owner="worker-1",
        preparation_id="preparation-1",
        evidence={"marker": "valid"},
    )
    assert repository.begin_cutover(
        scope=scope,
        migration_generation="generation-1",
        lease_owner="worker-1",
        preparation_id="preparation-1",
    )
    assert repository.mark_repair_required(
        scope=scope,
        migration_generation="generation-1",
        lease_owner="worker-1",
        failure_code="UNKNOWN",
        failure_stage="cutover_outcome_unknown",
        evidence={"reason": "response_lost"},
    )

    assert repository.resolve_repair(
        scope=scope,
        migration_generation="generation-1",
        operator="oncall-1",
        note="容器内已核验 bridge 指向 Pool",
        cutover_committed=True,
    )

    state = repository.get(scope)
    assert state.phase is SkillLayoutPhase.POOL_CUTOVER_COMMITTED
    assert state.data_plane_cutover_committed is True
    assert state.last_failure_code == "MANUAL_REPAIR_RESOLVED"
    assert state.last_failure_stage == "operator_resolution"
    assert state.last_failure_retryable is True
    assert state.last_failure_evidence == {
        "operator": "oncall-1",
        "note": "容器内已核验 bridge 指向 Pool",
        "cutover_committed": True,
        "previous_failure": {"reason": "response_lost"},
    }
    assert repository.try_acquire_lease(
        scope=scope,
        migration_generation="generation-1",
        lease_owner="worker-2",
        lease_seconds=60,
    )
    assert repository.record_post_cutover_evidence(
        scope=scope,
        migration_generation="generation-1",
        lease_owner="worker-2",
        preparation_id="preparation-1",
        evidence={
            "committed": True,
            "status": "ALREADY_COMMITTED",
            "evidence": {
                "quarantine": (
                    "/home/admin/.openclaw/workspace/skills-pool/"
                    ".migration-quarantine/generation-1/skills-local"
                )
            },
        },
    )
    with database.transactional_orm_session() as session:
        session.add(
            Skill(
                id=91,
                name="local-a",
                git_path="local:///legacy/local-a",
                bolt_id=scope.bot_id,
                env=scope.env,
            )
        )
    assert repository.commit_pool_active(
        scope=scope,
        migration_generation="generation-1",
        lease_owner="worker-2",
        preparation_id="preparation-1",
        local_locators={
            91: (
                "local:///home/admin/.openclaw/workspace/"
                "skills-pool/skills-local/local-a"
            )
        },
    )
    active = repository.get(scope)
    assert active.phase is SkillLayoutPhase.POOL_ACTIVE
    assert active.last_failure_code == "MANUAL_REPAIR_RESOLVED"
    assert active.last_failure_evidence["previous_failure"] == {
        "reason": "response_lost"
    }


def test_legacy_committed_repair_refresh_failure_keeps_refresh_phase() -> None:
    database = InMemorySqliteDB()
    repository = SkillsPoolLayoutRepository(database)
    scope = BotSkillLayoutScope(env="pre", entity_id="entity-1", bot_id="bot-1")
    repository.claim_pool_migration(
        scope=scope,
        layout_contract_version="skills-pool-p3-v1",
        migration_generation="generation-1",
        rollout_evidence=rollout_evidence(),
        lease_owner="worker-1",
        lease_seconds=60,
    )
    assert repository.record_ready_probe(
        scope=scope,
        migration_generation="generation-1",
        lease_owner="worker-1",
        preparation_id="preparation-1",
        evidence={"marker": "valid"},
    )
    assert repository.begin_cutover(
        scope=scope,
        migration_generation="generation-1",
        lease_owner="worker-1",
        preparation_id="preparation-1",
    )
    assert repository.mark_repair_required(
        scope=scope,
        migration_generation="generation-1",
        lease_owner="worker-1",
        failure_code="UNKNOWN",
        failure_stage="cutover_outcome_unknown",
        evidence={"reason": "response_lost"},
    )
    assert repository.resolve_repair(
        scope=scope,
        migration_generation="generation-1",
        operator="oncall-1",
        note="legacy backend resolved this row as committed",
        cutover_committed=True,
    )
    assert repository.try_acquire_lease(
        scope=scope,
        migration_generation="generation-1",
        lease_owner="worker-2",
        lease_seconds=60,
    )
    assert repository.record_post_cutover_failure(
        scope=scope,
        migration_generation="generation-1",
        lease_owner="worker-2",
        failure_code="TRANSIENT_ERROR",
        failure_stage="post_cutover_refresh",
        retryable=True,
        evidence={"reason": "adapter_temporarily_unreachable"},
    )

    state = repository.get(scope)
    assert state.phase is SkillLayoutPhase.POOL_CUTOVER_FINALIZING
    assert state.data_plane_cutover_committed is True
    assert state.last_failure_code == "TRANSIENT_ERROR"


def test_pool_active_commit_updates_only_all_local_rows_for_exact_bot() -> None:
    database = InMemorySqliteDB()
    repository = SkillsPoolLayoutRepository(database)
    scope = BotSkillLayoutScope(env="pre", entity_id="entity-1", bot_id="bot-1")
    repository.claim_pool_migration(
        scope=scope,
        layout_contract_version="skills-pool-p3-v1",
        migration_generation="generation-1",
        rollout_evidence=rollout_evidence(),
        lease_owner="worker-1",
        lease_seconds=60,
    )
    assert repository.record_ready_probe(
        scope=scope,
        migration_generation="generation-1",
        lease_owner="worker-1",
        preparation_id="preparation-1",
        evidence={"marker": "valid"},
    )
    assert repository.begin_cutover(
        scope=scope,
        migration_generation="generation-1",
        lease_owner="worker-1",
        preparation_id="preparation-1",
    )
    assert repository.record_cutover_committed(
        scope=scope,
        migration_generation="generation-1",
        lease_owner="worker-1",
        preparation_id="preparation-1",
        evidence={
            "committed": True,
            "status": "COMMITTED",
            "evidence": {
                "bridge": "valid",
                "quarantine": (
                    "/home/admin/.openclaw/workspace/skills-pool/"
                    ".migration-quarantine/generation-1/skills-local"
                ),
            },
        },
    )
    with database.transactional_orm_session() as session:
        session.add_all(
            [
                Skill(
                    id=1,
                    name="local-a",
                    git_path="local:///legacy/local-a",
                    bolt_id="bot-1",
                    env="pre",
                ),
                Skill(
                    id=2,
                    name="local-b",
                    git_path="local://local-b",
                    bolt_id="bot-1",
                    env="pre",
                ),
                Skill(
                    id=3,
                    name="repo",
                    git_path="git://business/repo",
                    bolt_id="bot-1",
                    env="pre",
                ),
                Skill(
                    id=4,
                    name="other-bot",
                    git_path="local:///legacy/other-bot",
                    bolt_id="bot-2",
                    env="pre",
                ),
                Skill(
                    id=5,
                    name="other-env",
                    git_path="local:///legacy/other-env",
                    bolt_id="bot-1",
                    env="prod",
                ),
            ]
        )

    committed = repository.commit_pool_active(
        scope=scope,
        migration_generation="generation-1",
        lease_owner="worker-1",
        preparation_id="preparation-1",
        local_locators={
            1: "local:///runtime/vendor-x/pool/local-a",
            2: "local:///runtime/vendor-x/pool/local-b",
        },
    )

    assert committed
    state = repository.get(scope)
    assert state.active_layout is SkillLayout.POOL
    assert state.target_layout is None
    assert state.phase is SkillLayoutPhase.POOL_ACTIVE
    assert state.pool_activated_at is not None
    assert state.lease_owner is None
    quarantine = repository.get_quarantine(scope, "generation-1")
    assert quarantine is not None
    assert quarantine.engine == "openclaw"
    assert quarantine.source_evidence["evidence"]["bridge"] == "valid"
    ready_observed_at = datetime.now(UTC) + timedelta(seconds=1)
    assert repository.record_runtime_reconciliation(
        scope=scope,
        migration_generation="generation-1",
        observed_at=ready_observed_at,
        evidence={"source": "arca_device_alive"},
    )
    reconciled = repository.get_quarantine(scope, "generation-1")
    assert reconciled is not None
    assert reconciled.runtime_reconciliation_status is RuntimeReconciliationStatus.READY
    assert reconciled.runtime_evidence == {"source": "arca_device_alive"}
    failed_observed_at = ready_observed_at + timedelta(seconds=1)
    assert repository.record_runtime_reconciliation_failure(
        scope=scope,
        migration_generation="generation-1",
        observed_at=failed_observed_at,
        evidence={"outcome": "invalid"},
    )
    failed = repository.get_quarantine(scope, "generation-1")
    assert failed is not None
    assert failed.runtime_reconciliation_status is RuntimeReconciliationStatus.FAILED
    assert failed.runtime_evidence == {"outcome": "invalid"}
    assert not repository.record_runtime_reconciliation(
        scope=scope,
        migration_generation="generation-1",
        observed_at=failed_observed_at,
        evidence={"source": "stale_retry"},
    )
    assert not repository.claim_cleanup(
        scope=scope,
        migration_generation="generation-1",
        cleanup_owner="cleanup-before-ready",
        lease_seconds=60,
        eligible_before=datetime.now(UTC),
    )
    assert repository.record_runtime_reconciliation(
        scope=scope,
        migration_generation="generation-1",
        observed_at=failed_observed_at + timedelta(microseconds=1),
        evidence={"source": "new_runtime"},
    )
    assert not repository.claim_cleanup(
        scope=scope,
        migration_generation="generation-1",
        cleanup_owner="cleanup-before-retention",
        lease_seconds=60,
        eligible_before=datetime.now(UTC) - timedelta(days=7),
    )
    assert repository.claim_cleanup(
        scope=scope,
        migration_generation="generation-1",
        cleanup_owner="cleanup-1",
        lease_seconds=60,
        eligible_before=datetime.now(UTC),
    )
    assert repository.record_cleanup_uncertain(
        scope=scope,
        migration_generation="generation-1",
        cleanup_owner="cleanup-1",
        evidence={"reason": "runtime_cleanup_outcome_unknown"},
    )
    cleaning = repository.get_quarantine(scope, "generation-1")
    assert cleaning is not None
    assert cleaning.status is QuarantineStatus.CLEANING
    assert cleaning.cleanup_lease_expires_at is not None
    assert not repository.begin_legacy_rollback(
        scope=scope,
        rollback_generation="rollback-1",
        operator="oncall",
        note="must wait for cleanup fence",
        lease_owner="rollback-worker",
        lease_seconds=60,
    )
    with database.transactional_orm_session() as session:
        session.query(SkillMigrationQuarantineModel).update(
            {
                SkillMigrationQuarantineModel.cleanup_lease_expires_at: (
                    datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=1)
                )
            }
        )
    assert repository.begin_legacy_rollback(
        scope=scope,
        rollback_generation="rollback-1",
        operator="oncall",
        note="expired cleanup fence may be taken over",
        lease_owner="rollback-worker",
        lease_seconds=60,
    )
    assert not repository.mark_cleaned(
        scope=scope,
        migration_generation="generation-1",
        cleanup_owner="cleanup-1",
        evidence={"path_absent": True},
    )
    with database.transactional_orm_session() as session:
        paths = {
            row.id: row.git_path
            for row in session.query(Skill).order_by(Skill.id).all()
        }
    assert paths == {
        1: "local:///runtime/vendor-x/pool/local-a",
        2: "local:///runtime/vendor-x/pool/local-b",
        3: "git://business/repo",
        4: "local:///legacy/other-bot",
        5: "local:///legacy/other-env",
    }


def test_pool_active_commit_rejects_partial_or_stale_local_locator_set() -> None:
    database = InMemorySqliteDB()
    repository = SkillsPoolLayoutRepository(database)
    scope = BotSkillLayoutScope(env="pre", entity_id="entity-1", bot_id="bot-1")
    repository.claim_pool_migration(
        scope=scope,
        layout_contract_version="skills-pool-p3-v1",
        migration_generation="generation-1",
        rollout_evidence=rollout_evidence(),
        lease_owner="worker-1",
        lease_seconds=60,
    )
    assert repository.record_ready_probe(
        scope=scope,
        migration_generation="generation-1",
        lease_owner="worker-1",
        preparation_id="preparation-1",
        evidence={
            "committed": True,
            "status": "COMMITTED",
            "evidence": {
                "quarantine": (
                    "/home/admin/.openclaw/workspace/skills-pool/"
                    ".migration-quarantine/generation-1/skills-local"
                )
            },
        },
    )
    assert repository.begin_cutover(
        scope=scope,
        migration_generation="generation-1",
        lease_owner="worker-1",
        preparation_id="preparation-1",
    )
    assert repository.record_cutover_committed(
        scope=scope,
        migration_generation="generation-1",
        lease_owner="worker-1",
        preparation_id="preparation-1",
        evidence={
            "committed": True,
            "status": "COMMITTED",
            "evidence": {
                "quarantine": (
                    "/home/admin/.openclaw/workspace/skills-pool/"
                    ".migration-quarantine/generation-1/skills-local"
                )
            },
        },
    )
    with database.transactional_orm_session() as session:
        session.add_all(
            [
                Skill(
                    id=1,
                    name="local-a",
                    git_path="local:///legacy/local-a",
                    bolt_id="bot-1",
                    env="pre",
                ),
                Skill(
                    id=2,
                    name="local-b",
                    git_path="local:///legacy/local-b",
                    bolt_id="bot-1",
                    env="pre",
                ),
            ]
        )

    assert not repository.commit_pool_active(
        scope=scope,
        migration_generation="generation-1",
        lease_owner="worker-1",
        preparation_id="preparation-1",
        local_locators={
            1: (
                "local:///home/admin/.openclaw/workspace/"
                "skills-pool/skills-local/local-a"
            )
        },
    )
    assert repository.get(scope).active_layout is SkillLayout.LEGACY


def test_explicit_rollback_is_fenced_and_commits_locator_atomically() -> None:
    database = InMemorySqliteDB()
    repository = SkillsPoolLayoutRepository(database)
    scope = BotSkillLayoutScope(env="pre", entity_id="entity-1", bot_id="bot-1")
    with database.transactional_orm_session() as session:
        session.add(
            BotSkillLayoutStateModel(
                env=scope.env,
                entity_id=scope.entity_id,
                bot_id=scope.bot_id,
                active_layout=SkillLayout.POOL.value,
                phase=SkillLayoutPhase.POOL_ACTIVE.value,
                layout_contract_version="skills-pool-p3-v1",
                preparation_id="preparation-1",
                migration_generation="migration-1",
                data_plane_cutover_committed=1,
            )
        )
        session.add(
            Skill(
                id=101,
                env=scope.env,
                bolt_id=scope.bot_id,
                user_id="owner-1",
                name="local-a",
                git_path=(
                    "local:///home/admin/.openclaw/workspace/"
                    "skills-pool/skills-local/local-a"
                ),
            )
        )

    assert repository.begin_legacy_rollback(
        scope=scope,
        rollback_generation="rollback-1",
        operator="oncall-1",
        note="业务回滚",
        lease_owner="rollback-worker",
        lease_seconds=60,
    )
    preparing = repository.get(scope)
    assert preparing.phase is SkillLayoutPhase.LEGACY_ROLLBACK_PREPARING
    assert preparing.target_layout is SkillLayout.LEGACY
    assert preparing.migration_generation == "rollback-1"
    assert preparing.last_failure_stage == "rollback_requested"

    assert repository.record_legacy_rollback_committed(
        scope=scope,
        rollback_generation="rollback-1",
        lease_owner="rollback-worker",
        evidence={"source": "current_pool"},
    )
    assert repository.record_rollback_failure(
        scope=scope,
        rollback_generation="rollback-1",
        lease_owner="rollback-worker",
        failure_code="ROLLBACK_MAPPING_PUBLISH_FAILED",
        failure_stage="mapping_publish",
        retryable=True,
        evidence={"mapping_count": 1},
    )
    committed = repository.get(scope)
    assert committed.phase is SkillLayoutPhase.LEGACY_ROLLBACK_COMMITTED
    assert committed.last_failure_code == "ROLLBACK_MAPPING_PUBLISH_FAILED"
    assert committed.last_failure_at is not None
    with database.transactional_orm_session() as session:
        session.query(BotSkillLayoutStateModel).filter(
            *repository._scope_filter(scope)
        ).update(
            {
                BotSkillLayoutStateModel.lease_expires_at: func.datetime(
                    func.now(), text("'-1 second'")
                )
            }
        )
    assert repository.try_acquire_rollback_lease(
        scope=scope,
        rollback_generation="rollback-1",
        lease_owner="replacement-worker",
        lease_seconds=60,
    )

    locator = "local:///home/admin/.openclaw/workspace/skills/skills-local/local-a"
    assert repository.commit_legacy_active(
        scope=scope,
        rollback_generation="rollback-1",
        lease_owner="replacement-worker",
        local_locators={101: locator},
    )
    state = repository.get(scope)
    assert state.active_layout is SkillLayout.LEGACY
    assert state.target_layout is None
    assert state.phase is SkillLayoutPhase.LEGACY_ACTIVE
    assert state.layout_contract_version is None
    assert state.preparation_id is None
    assert state.lease_owner is None
    with database.transactional_orm_session() as session:
        assert session.get(Skill, 101).git_path == locator


def test_explicit_rollback_rejects_partial_local_locator_commit() -> None:
    database = InMemorySqliteDB()
    repository = SkillsPoolLayoutRepository(database)
    scope = BotSkillLayoutScope(env="pre", entity_id="entity-1", bot_id="bot-1")
    with database.transactional_orm_session() as session:
        session.add(
            BotSkillLayoutStateModel(
                env=scope.env,
                entity_id=scope.entity_id,
                bot_id=scope.bot_id,
                active_layout=SkillLayout.POOL.value,
                target_layout=SkillLayout.LEGACY.value,
                phase=SkillLayoutPhase.LEGACY_ROLLBACK_COMMITTED.value,
                migration_generation="rollback-1",
                lease_owner="rollback-worker",
                lease_expires_at=func.datetime(func.now(), text("'+60 seconds'")),
            )
        )
        for skill_id, name in ((101, "local-a"), (102, "local-b")):
            session.add(
                Skill(
                    id=skill_id,
                    env=scope.env,
                    bolt_id=scope.bot_id,
                    user_id="owner-1",
                    name=name,
                    git_path=f"local:///pool/{name}",
                )
            )

    assert not repository.commit_legacy_active(
        scope=scope,
        rollback_generation="rollback-1",
        lease_owner="rollback-worker",
        local_locators={
            101: ("local:///home/admin/.openclaw/workspace/skills/skills-local/local-a")
        },
    )
    assert repository.get(scope).phase is SkillLayoutPhase.LEGACY_ROLLBACK_COMMITTED
