"""Tenant isolation for Skills Pool rollout audit and quarantine records."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from agentclaw.community.core.skills_pool.repository.models import (
    SkillMigrationQuarantineModel,
    SkillsPoolRolloutAuditModel,
)
from agentclaw.community.core.skills_pool.types import BotSkillLayoutScope
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


@pytest.mark.parametrize(
    ("model", "factory", "identity_filter", "update_values"),
    [
        (
            SkillsPoolRolloutAuditModel,
            _audit,
            lambda item: item.effective_config_version == "revision-1",
            {"action": "disabled"},
        ),
        (
            SkillMigrationQuarantineModel,
            _quarantine,
            lambda item: item.migration_generation == "generation-1",
            {"status": "cleaned"},
        ),
    ],
)
def test_control_plane_records_are_stamped_and_direct_orm_reads_are_tenant_scoped(
    database,
    model,
    factory,
    identity_filter,
    update_values,
) -> None:
    with avernet_tenant_scope("tenant-a"):
        with database.transactional_orm_session() as session:
            session.add(factory())

    with avernet_tenant_scope("tenant-b"):
        with database.transactional_orm_session() as session:
            assert session.query(model).filter(identity_filter(model)).all() == []

    with database.transactional_orm_session() as session:
        row = (
            session.query(model).execution_options(skip_avernet_tenant_guard=True).one()
        )
        assert row.avernet_tenant == "tenant-a"


@pytest.mark.parametrize(
    ("model", "factory", "identity_filter", "update_values"),
    [
        (
            SkillsPoolRolloutAuditModel,
            _audit,
            lambda item: item.effective_config_version == "revision-1",
            {"action": "disabled"},
        ),
        (
            SkillMigrationQuarantineModel,
            _quarantine,
            lambda item: item.migration_generation == "generation-1",
            {"status": "cleaned"},
        ),
    ],
)
def test_cross_tenant_control_plane_update_and_delete_are_noops(
    database,
    model,
    factory,
    identity_filter,
    update_values,
) -> None:
    with avernet_tenant_scope("tenant-a"):
        with database.transactional_orm_session() as session:
            session.add(factory())

    with avernet_tenant_scope("tenant-b"):
        with database.transactional_orm_session() as session:
            query = session.query(model).filter(identity_filter(model))
            assert query.update(update_values, synchronize_session=False) == 0
            assert query.delete(synchronize_session=False) == 0

    with avernet_tenant_scope("tenant-a"):
        with database.transactional_orm_session() as session:
            assert session.query(model).filter(identity_filter(model)).count() == 1


@pytest.mark.parametrize(
    ("model", "factory", "identity_filter", "update_values"),
    [
        (
            SkillsPoolRolloutAuditModel,
            _audit,
            lambda item: item.effective_config_version == "revision-1",
            {"action": "disabled"},
        ),
        (
            SkillMigrationQuarantineModel,
            _quarantine,
            lambda item: item.migration_generation == "generation-1",
            {"status": "cleaned"},
        ),
    ],
)
def test_own_tenant_can_update_and_delete_control_plane_records(
    database,
    model,
    factory,
    identity_filter,
    update_values,
) -> None:
    with avernet_tenant_scope("tenant-a"):
        with database.transactional_orm_session() as session:
            session.add(factory())

        with database.transactional_orm_session() as session:
            query = session.query(model).filter(identity_filter(model))
            assert query.update(update_values, synchronize_session=False) == 1

        with database.transactional_orm_session() as session:
            query = session.query(model).filter(identity_filter(model))
            assert query.delete(synchronize_session=False) == 1

        with database.transactional_orm_session() as session:
            assert session.query(model).filter(identity_filter(model)).count() == 0


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


def test_quarantine_identity_is_unique_per_tenant_and_serialization_hides_tenant(
    database,
) -> None:
    for tenant in ("tenant-a", "tenant-b"):
        with avernet_tenant_scope(tenant):
            with database.transactional_orm_session() as session:
                row = _quarantine()
                row.pool_activated_at = datetime.now(UTC).replace(tzinfo=None)
                session.add(row)

    with avernet_tenant_scope("tenant-a"):
        with database.transactional_orm_session() as session:
            record = session.query(SkillMigrationQuarantineModel).one().to_record()
    assert record.scope == BotSkillLayoutScope("pre", "owner-1", "bot-1")
    assert not hasattr(record, "avernet_tenant")

    with avernet_tenant_scope("tenant-a"):
        with pytest.raises(IntegrityError):
            with database.transactional_orm_session() as session:
                session.add(_quarantine())
                session.flush()


@pytest.mark.parametrize(
    ("model", "values"),
    [
        (
            SkillsPoolRolloutAuditModel,
            {
                "env": "pre",
                "config_id": 1,
                "action": "enable",
                "operator": "operator",
                "reason": "default tenant",
                "effective_config_version": "raw-default",
            },
        ),
        (
            SkillMigrationQuarantineModel,
            {
                "env": "pre",
                "entity_id": "owner-raw",
                "bot_id": "bot-raw",
                "migration_generation": "raw-default",
                "engine": "openclaw",
                "path": "/quarantine/raw-default",
                "source_evidence": "{}",
            },
        ),
    ],
)
def test_raw_control_plane_inserts_default_to_teamclaw(database, model, values) -> None:
    with database.transactional_orm_session() as session:
        session.execute(insert(model).values(**values))
        row = (
            session.query(model).execution_options(skip_avernet_tenant_guard=True).one()
        )
        assert row.avernet_tenant == "teamclaw"


@pytest.mark.parametrize("factory", [_audit, _quarantine])
def test_control_plane_insert_rejects_an_explicit_conflicting_tenant(
    database,
    factory,
) -> None:
    with avernet_tenant_scope("tenant-a"):
        with pytest.raises(CrossTenantInsertError):
            with database.transactional_orm_session() as session:
                row = factory()
                row.avernet_tenant = "tenant-b"
                session.add(row)
                session.flush()
