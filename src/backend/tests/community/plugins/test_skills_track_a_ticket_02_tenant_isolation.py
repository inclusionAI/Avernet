"""Tenant isolation for Skills Track A ticket 02 persistence records."""
from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from agentclaw.community.core.skills_pool.repository.models import (
    BotSkillLayoutStateModel,
)
from agentclaw.community.core.skills_pool.types import SkillLayout
from agentclaw.community.plugins.local.sqlite_models import (
    DefaultSkillsetMcpExclusion,
    DefaultSkillsetSkillExclusion,
)
from agentclaw.community.plugins.skill_repository import SkillSetRepository
from agentclaw.community.utils.avernet_tenant import avernet_tenant_scope
from agentclaw.community.utils.avernet_tenant_guard import CrossTenantInsertError

pytestmark = pytest.mark.integration


class _FileSqliteDB:
    def __init__(self, engine):
        self._factory = sessionmaker(bind=engine, autoflush=False)

    @contextmanager
    def session(self):
        session = self._factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    orm_session = session


@pytest.fixture
def db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'skills-ticket-02.db'}")
    for model in (
        DefaultSkillsetMcpExclusion,
        DefaultSkillsetSkillExclusion,
        BotSkillLayoutStateModel,
    ):
        model.__table__.create(engine)
    return _FileSqliteDB(engine)


def _new_row(model, *, suffix="shared"):
    if model is DefaultSkillsetMcpExclusion:
        return model(
            user_id="user-1", bot_id="bot-1", skill_set_id=7,
            server_code=f"mcp.{suffix}",
        )
    if model is DefaultSkillsetSkillExclusion:
        return model(
            user_id="user-1", bot_id="bot-1", skill_set_id=7, skill_id=42,
        )
    return model(env="pre", entity_id="entity-1", bot_id=f"bot-{suffix}")


def _raw_insert_values(model):
    if model is DefaultSkillsetMcpExclusion:
        return {
            "user_id": "user-1",
            "bot_id": "bot-1",
            "skill_set_id": 7,
            "server_code": "mcp.raw",
        }
    if model is DefaultSkillsetSkillExclusion:
        return {
            "user_id": "user-1",
            "bot_id": "bot-1",
            "skill_set_id": 7,
            "skill_id": 42,
        }
    return {"env": "pre", "entity_id": "entity-1", "bot_id": "bot-raw"}


@pytest.mark.parametrize(
    "model", (DefaultSkillsetMcpExclusion, DefaultSkillsetSkillExclusion, BotSkillLayoutStateModel),
)
def test_current_tenant_stamps_insert_and_rejects_conflicting_insert(db, model):
    with avernet_tenant_scope("tenant-a"):
        with db.session() as session:
            session.add(_new_row(model))

    with db.session() as session:
        stored = session.query(model).execution_options(
            skip_avernet_tenant_guard=True
        ).one()
        assert stored.avernet_tenant == "tenant-a"

    with avernet_tenant_scope("tenant-b"):
        with pytest.raises(CrossTenantInsertError, match=model.__name__):
            with db.session() as session:
                conflicting = _new_row(model, suffix="conflict")
                conflicting.avernet_tenant = "tenant-a"
                session.add(conflicting)
                session.flush()


@pytest.mark.parametrize(
    "model", (DefaultSkillsetMcpExclusion, DefaultSkillsetSkillExclusion, BotSkillLayoutStateModel),
)
def test_raw_insert_omitting_tenant_uses_server_default(db, model):
    """Core inserts bypass ``before_insert`` and must retain compatibility."""
    with db.session() as session:
        session.execute(model.__table__.insert().values(**_raw_insert_values(model)))

    with db.session() as session:
        stored = session.query(model).execution_options(
            skip_avernet_tenant_guard=True
        ).one()
        assert stored.avernet_tenant == "teamclaw"


@pytest.mark.parametrize(
    "model", (DefaultSkillsetMcpExclusion, DefaultSkillsetSkillExclusion, BotSkillLayoutStateModel),
)
def test_direct_orm_queries_and_mutations_are_tenant_scoped(db, model):
    with avernet_tenant_scope("tenant-a"):
        with db.session() as session:
            own = _new_row(model, suffix="a")
            session.add(own)
            session.flush()
            own_id = own.id
    with avernet_tenant_scope("tenant-b"):
        with db.session() as session:
            foreign = _new_row(model, suffix="b")
            session.add(foreign)
            session.flush()
            foreign_id = foreign.id

    with avernet_tenant_scope("tenant-a"):
        with db.session() as session:
            assert session.query(model).count() == 1
            assert (
                session.query(model)
                .filter(model.id == foreign_id)
                .update({model.gmt_modified: func.now()}, synchronize_session=False)
                == 0
            )
            assert (
                session.query(model)
                .filter(model.id == foreign_id)
                .delete(synchronize_session=False)
                == 0
            )
            assert session.query(model).filter(model.id == own_id).count() == 1

    with avernet_tenant_scope("tenant-b"):
        with db.session() as session:
            assert session.query(model).count() == 1


@pytest.mark.parametrize(
    "model", (DefaultSkillsetMcpExclusion, DefaultSkillsetSkillExclusion, BotSkillLayoutStateModel),
)
def test_tenant_local_business_identity_can_repeat_across_tenants(db, model):
    for tenant in ("tenant-a", "tenant-b"):
        with avernet_tenant_scope(tenant):
            with db.session() as session:
                session.add(_new_row(model))

    with avernet_tenant_scope("tenant-a"):
        with pytest.raises(IntegrityError):
            with db.session() as session:
                session.add(_new_row(model))
                session.flush()


def test_layout_state_value_object_does_not_serialize_tenant(db):
    with avernet_tenant_scope("tenant-a"):
        with db.session() as session:
            session.add(_new_row(BotSkillLayoutStateModel))

    with avernet_tenant_scope("tenant-a"):
        with db.session() as session:
            state = session.query(BotSkillLayoutStateModel).one().to_state()

    assert state.active_layout is SkillLayout.LEGACY
    assert not hasattr(state, "avernet_tenant")


def test_default_exclusion_repository_upserts_by_current_tenant(db):
    repository = SkillSetRepository(db)

    for tenant in ("tenant-a", "tenant-b"):
        with avernet_tenant_scope(tenant):
            assert repository.add_default_mcp_exclusion("user-1", "bot-1", 7, "mcp.a")
            assert repository.add_default_skill_exclusion("user-1", "bot-1", 7, 42)

    for tenant in ("tenant-a", "tenant-b"):
        with avernet_tenant_scope(tenant):
            assert repository.get_excluded_mcps("user-1", "bot-1", 7) == ["mcp.a"]
            assert repository.get_excluded_skills("user-1", "bot-1", 7) == [42]
