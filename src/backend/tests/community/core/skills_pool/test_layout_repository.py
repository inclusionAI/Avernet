"""Skills Pool 布局状态仓库的行为契约测试。"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from threading import Barrier

from sqlalchemy import create_engine, func, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from agentclaw.community.core.base import Base
from agentclaw.community.core.skills_pool.types import (
    BotSkillLayoutScope,
    RolloutEvidence,
    SkillLayout,
    SkillLayoutPhase,
)
from agentclaw.community.core.skills_pool.repository.models import (
    BotSkillLayoutStateModel,
)
from agentclaw.community.core.models.skill import Skill
from agentclaw.community.plugins.skills_pool_layout_repository import (
    SkillsPoolLayoutRepository,
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


def rollout_evidence() -> RolloutEvidence:
    return RolloutEvidence(
        env="pre",
        config_id=42,
        config_version="2026-07-23T12:00:00",
        batch_id="openclaw-canary-1",
        engine_type="openclaw",
        decision_reason="exact_bot_match",
    )


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
        evidence={"bridge": "valid"},
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
            1: (
                "local:///home/admin/.openclaw/workspace/"
                "skills-pool/skills-local/local-a"
            ),
            2: (
                "local:///home/admin/.openclaw/workspace/"
                "skills-pool/skills-local/local-b"
            ),
        },
    )

    assert committed
    state = repository.get(scope)
    assert state.active_layout is SkillLayout.POOL
    assert state.target_layout is None
    assert state.phase is SkillLayoutPhase.POOL_ACTIVE
    assert state.pool_activated_at is not None
    assert state.lease_owner is None
    with database.transactional_orm_session() as session:
        paths = {
            row.id: row.git_path
            for row in session.query(Skill).order_by(Skill.id).all()
        }
    assert paths == {
        1: (
            "local:///home/admin/.openclaw/workspace/"
            "skills-pool/skills-local/local-a"
        ),
        2: (
            "local:///home/admin/.openclaw/workspace/"
            "skills-pool/skills-local/local-b"
        ),
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
        evidence={},
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
        evidence={},
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
