"""Tenant isolation for the Skills catalog and Skill Set association rows."""
from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from agentclaw.community.core.models import Skill, SkillSet, SkillSetMCPServer, SkillSetSkill
from agentclaw.community.utils.avernet_tenant import (
    avernet_tenant_scope,
)
from agentclaw.community.utils.avernet_tenant_guard import CrossTenantInsertError


MODELS = (Skill, SkillSet, SkillSetSkill, SkillSetMCPServer)
UPDATE_VALUES = {
    Skill: {"name": "changed"},
    SkillSet: {"name": "changed"},
    SkillSetSkill: {"skill_uuid": "changed"},
    SkillSetMCPServer: {"name": "changed"},
}


class _SqliteDB:
    def __init__(self, engine):
        self._factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)

    @contextmanager
    def orm_session(self):
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
def db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'skill-catalog.db'}")
    for model in MODELS:
        model.__table__.create(engine)
    return _SqliteDB(engine)


def _seed_catalog(session_factory, tenant: str, suffix: str) -> tuple[int, int, int, int]:
    with avernet_tenant_scope(tenant):
        with session_factory.orm_session() as session:
            skill = Skill(name=f"skill-{suffix}")
            skill_set = SkillSet(name=f"set-{suffix}")
            session.add_all((skill, skill_set))
            session.flush()
            association = SkillSetSkill(skill_set_id=skill_set.id, skill_id=skill.id)
            mcp = SkillSetMCPServer(
                skill_set_id=skill_set.id,
                server_code=f"server-{suffix}",
                name=f"MCP {suffix}",
            )
            session.add_all((association, mcp))
            session.flush()
            return skill.id, skill_set.id, association.id, mcp.id


def test_catalog_models_stamp_current_tenant_and_keep_serializers_unchanged(db):
    ids = _seed_catalog(db, "tenant-a", "a")

    with avernet_tenant_scope("tenant-a"):
        with db.orm_session() as session:
            rows = [session.get(model, row_id) for model, row_id in zip(MODELS, ids)]
            assert [row.avernet_tenant for row in rows] == ["tenant-a"] * len(MODELS)
            assert all("avernet_tenant" not in row.to_dict() for row in rows)


def test_catalog_models_filter_direct_queries_and_cross_tenant_writes(db):
    own_ids = _seed_catalog(db, "tenant-a", "a")
    _seed_catalog(db, "tenant-b", "b")

    with avernet_tenant_scope("tenant-a"):
        with db.orm_session() as session:
            assert [session.query(model).count() for model in MODELS] == [1] * len(MODELS)

    with avernet_tenant_scope("tenant-b"):
        with db.orm_session() as session:
            for model, row_id in zip(MODELS, own_ids):
                assert session.query(model).filter(model.id == row_id).delete() == 0
                assert (
                    session.query(model)
                    .filter(model.id == row_id)
                    .update(UPDATE_VALUES[model])
                    == 0
                )

    with avernet_tenant_scope("tenant-a"):
        with db.orm_session() as session:
            assert [session.get(model, row_id) is not None for model, row_id in zip(MODELS, own_ids)] == [True] * len(MODELS)
            for model, row_id in zip(MODELS, own_ids):
                assert (
                    session.query(model)
                    .filter(model.id == row_id)
                    .update(UPDATE_VALUES[model])
                    == 1
                )
            for model, row_id in zip(reversed(MODELS), reversed(own_ids)):
                assert session.query(model).filter(model.id == row_id).delete() == 1


@pytest.mark.parametrize("model, kwargs", [
    (Skill, {"name": "conflicting-skill"}),
    (SkillSet, {"name": "conflicting-set"}),
    (SkillSetSkill, {"skill_set_id": 1, "skill_id": 1}),
    (SkillSetMCPServer, {"skill_set_id": 1, "server_code": "conflicting", "name": "Conflicting"}),
])
def test_catalog_models_reject_explicit_cross_tenant_insert(db, model, kwargs):
    with avernet_tenant_scope("tenant-a"):
        with pytest.raises(CrossTenantInsertError):
            with db.orm_session() as session:
                session.add(model(**kwargs, avernet_tenant="tenant-b"))
                session.flush()


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO ac_skill (name, env, is_public, is_builtin, gmt_created, gmt_modified) "
        "VALUES ('raw-skill', 'dev', 0, 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
        "INSERT INTO ac_skill_set (name, env, is_default, is_builtin, is_active, gmt_created, gmt_modified) "
        "VALUES ('raw-set', 'dev', 0, 0, 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
        "INSERT INTO ac_skill_set_skill (skill_set_id, skill_id, env, gmt_created, gmt_modified) "
        "VALUES (99, 99, 'dev', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
        "INSERT INTO ac_skill_set_mcp (skill_set_id, server_code, name) VALUES (99, 'raw-mcp', 'Raw MCP')",
    ],
)
def test_catalog_raw_writers_receive_teamclaw_server_default(db, sql):
    """Non-ORM cutover writers retain the internal tenant through DDL default."""
    with db.orm_session() as session:
        session.execute(text(sql))
        table_name = sql.split(" ", 3)[2]
        assert session.execute(
            text(f"SELECT avernet_tenant FROM {table_name}")
        ).scalar_one() == "teamclaw"
