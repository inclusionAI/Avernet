"""Tenant isolation for Skills Pool rollout audit and quarantine records."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from agentclaw.community.core.skills_pool.repository.models import (
    BotSkillLayoutStateModel,
    SkillMigrationQuarantineModel,
    SkillsPoolRolloutAuditModel,
)
from agentclaw.community.core.skills_pool.types import (
    BotSkillLayoutScope,
    SkillLayout,
    SkillLayoutPhase,
)
from agentclaw.community.plugins.skills_pool_rollout_repository import (
    SkillsPoolRolloutRepository,
)
from agentclaw.community.utils.avernet_tenant import avernet_tenant_scope
from agentclaw.community.utils.avernet_tenant_guard import CrossTenantInsertError

pytestmark = pytest.mark.integration


class _SqliteDatabase:
    def __init__(self, engine) -> None:
        self._factory = sessionmaker(bind=engine, autoflush=False)

    @contextmanager
    def transactional_orm_session(self):
        session = self._factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()


@pytest.fixture
def database(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'skills-pool-control.db'}")
    BotSkillLayoutStateModel.__table__.create(engine)
    SkillsPoolRolloutAuditModel.__table__.create(engine)
    SkillMigrationQuarantineModel.__table__.create(engine)
    return _SqliteDatabase(engine)


def _audit(revision: str = "revision-1") -> SkillsPoolRolloutAuditModel:
    return SkillsPoolRolloutAuditModel(
        env="pre",
        config_id=1,
        action="enable",
        operator="operator",
        reason="start canary",
        effective_config_version=revision,
    )


def _quarantine(
    generation: str = "generation-1",
) -> SkillMigrationQuarantineModel:
    return SkillMigrationQuarantineModel(
        env="pre",
        entity_id="owner-1",
        bot_id="bot-1",
        migration_generation=generation,
        engine="openclaw",
        path="/quarantine/generation-1",
        source_evidence="{}",
    )


def _layout() -> BotSkillLayoutStateModel:
    return BotSkillLayoutStateModel(
        env="pre",
        entity_id="owner-1",
        bot_id="bot-1",
        active_layout=SkillLayout.LEGACY.value,
        phase=SkillLayoutPhase.LEGACY_ACTIVE.value,
    )


def test_rollout_audit_is_stamped_and_direct_orm_reads_are_tenant_scoped(
    database,
) -> None:
    with avernet_tenant_scope("tenant-a"):
        with database.transactional_orm_session() as session:
            session.add(_audit())

    with avernet_tenant_scope("tenant-b"):
        with database.transactional_orm_session() as session:
            assert session.query(SkillsPoolRolloutAuditModel).all() == []

    with database.transactional_orm_session() as session:
        row = (
            session.query(SkillsPoolRolloutAuditModel)
            .execution_options(skip_avernet_tenant_guard=True)
            .one()
        )
        assert row.avernet_tenant == "tenant-a"


def test_cross_tenant_rollout_audit_update_and_delete_are_noops(database) -> None:
    with avernet_tenant_scope("tenant-a"):
        with database.transactional_orm_session() as session:
            session.add(_audit())

    with avernet_tenant_scope("tenant-b"):
        with database.transactional_orm_session() as session:
            query = session.query(SkillsPoolRolloutAuditModel).filter(
                SkillsPoolRolloutAuditModel.effective_config_version == "revision-1"
            )
            assert query.update({"action": "disabled"}, synchronize_session=False) == 0
            assert query.delete(synchronize_session=False) == 0

    with avernet_tenant_scope("tenant-a"):
        with database.transactional_orm_session() as session:
            assert session.query(SkillsPoolRolloutAuditModel).count() == 1


def test_own_tenant_can_update_and_delete_rollout_audit(database) -> None:
    with avernet_tenant_scope("tenant-a"):
        with database.transactional_orm_session() as session:
            session.add(_audit())

        with database.transactional_orm_session() as session:
            query = session.query(SkillsPoolRolloutAuditModel).filter(
                SkillsPoolRolloutAuditModel.effective_config_version == "revision-1"
            )
            assert query.update({"action": "disabled"}, synchronize_session=False) == 1

        with database.transactional_orm_session() as session:
            query = session.query(SkillsPoolRolloutAuditModel).filter(
                SkillsPoolRolloutAuditModel.effective_config_version == "revision-1"
            )
            assert query.delete(synchronize_session=False) == 1

        with database.transactional_orm_session() as session:
            assert session.query(SkillsPoolRolloutAuditModel).count() == 0


def test_audit_revision_is_unique_per_tenant_and_serialization_hides_tenant(
    database,
) -> None:
    for tenant in ("tenant-a", "tenant-b"):
        with avernet_tenant_scope(tenant):
            with database.transactional_orm_session() as session:
                session.add(_audit())

    repository = SkillsPoolRolloutRepository(database)
    with avernet_tenant_scope("tenant-a"):
        events = repository.list_audit_events(env="pre")
    assert [event["effective_config_version"] for event in events] == ["revision-1"]
    assert "avernet_tenant" not in events[0]

    with avernet_tenant_scope("tenant-a"):
        with pytest.raises(IntegrityError):
            with database.transactional_orm_session() as session:
                session.add(_audit())
                session.flush()


@pytest.mark.parametrize(
    ("model", "factory"),
    [
        (BotSkillLayoutStateModel, _layout),
        (SkillMigrationQuarantineModel, _quarantine),
    ],
)
def test_bot_scoped_control_records_are_global_and_have_no_tenant_column(
    database,
    model,
    factory,
) -> None:
    assert "avernet_tenant" not in model.__table__.c

    for tenant in ("tenant-a", "tenant-b"):
        with avernet_tenant_scope(tenant):
            if tenant == "tenant-a":
                with database.transactional_orm_session() as session:
                    row = factory()
                    if isinstance(row, SkillMigrationQuarantineModel):
                        row.pool_activated_at = datetime.now(UTC).replace(tzinfo=None)
                    session.add(row)
            else:
                with pytest.raises(IntegrityError):
                    with database.transactional_orm_session() as session:
                        session.add(factory())
                        session.flush()

    if model is SkillMigrationQuarantineModel:
        with database.transactional_orm_session() as session:
            record = session.query(model).one().to_record()
        assert record.scope == BotSkillLayoutScope("pre", "owner-1", "bot-1")

def test_raw_rollout_audit_inserts_default_to_teamclaw(database) -> None:
    with database.transactional_orm_session() as session:
        session.execute(insert(SkillsPoolRolloutAuditModel).values(
            env="pre", config_id=1, action="enable", operator="operator",
            reason="default tenant", effective_config_version="raw-default",
        ))
        row = (
            session.query(SkillsPoolRolloutAuditModel)
            .execution_options(skip_avernet_tenant_guard=True)
            .one()
        )
        assert row.avernet_tenant == "teamclaw"


def test_rollout_audit_insert_rejects_an_explicit_conflicting_tenant(database) -> None:
    with avernet_tenant_scope("tenant-a"):
        with pytest.raises(CrossTenantInsertError):
            with database.transactional_orm_session() as session:
                row = _audit()
                row.avernet_tenant = "tenant-b"
                session.add(row)
                session.flush()
