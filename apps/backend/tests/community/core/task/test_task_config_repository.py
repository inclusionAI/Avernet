"""Repository tests for ``task_config`` upsert + get_by_name (spec 2026-11-02).

Covers ReviewFocus #3 (upsert preserves creator + gmt_create) and #4
(env isolation) at the repository layer, plus the type-overwrite and
absent-row paths the service layer depends on.
"""

from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from agentclaw.community.core.base import Base
from agentclaw.community.core.schema import import_all_models
from agentclaw.community.core.task.task_config.repository import TaskConfigRepository


class _InMemorySqliteDB:
    """Minimal DatabasePlugin stand-in: an orm_session() that commits on clean
    exit (mirrors tests/community/repository/task/conftest.py::InMemorySqliteDB)."""

    def __init__(self, engine):
        self._factory = sessionmaker(bind=engine, autoflush=False)

    @contextmanager
    def orm_session(self):
        db = self._factory()
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()


def _repo_with_memory_db() -> TaskConfigRepository:
    import_all_models()
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return TaskConfigRepository(db=_InMemorySqliteDB(engine))


def test_upsert_insert_sets_creator_and_modifier():
    repo = _repo_with_memory_db()
    rid = repo.upsert(
        config_name="skill_set",
        config_type="set",
        config_value='["a"]',
        env="pre",
        operator="u1",
    )
    assert rid >= 1
    row = repo.get_by_name(env="pre", config_name="skill_set")
    assert row is not None
    assert row.creator == "u1"
    assert row.modifier == "u1"
    assert row.config_value == '["a"]'


def test_upsert_preserves_creator_and_gmt_create():
    repo = _repo_with_memory_db()
    repo.upsert(
        config_name="x",
        config_type="single_string",
        config_value='"v1"',
        env="pre",
        operator="u1",
    )
    first = repo.get_by_name(env="pre", config_name="x")
    assert first is not None
    repo.upsert(
        config_name="x",
        config_type="single_string",
        config_value='"v2"',
        env="pre",
        operator="u2",
    )
    second = repo.get_by_name(env="pre", config_name="x")
    assert second is not None
    assert second.config_value == '"v2"'
    assert second.creator == first.creator == "u1"  # creator not overwritten
    assert second.modifier == "u2"  # modifier refreshed
    assert second.gmt_create == first.gmt_create  # gmt_create not overwritten
    assert second.gmt_modify >= first.gmt_modify  # gmt_modify refreshed (>= same-second)


def test_upsert_changes_type_on_overwrite():
    repo = _repo_with_memory_db()
    repo.upsert(config_name="x", config_type="bool", config_value="true", env="pre")
    repo.upsert(config_name="x", config_type="set", config_value='["a"]', env="pre")
    row = repo.get_by_name(env="pre", config_name="x")
    assert row is not None
    assert row.config_type == "set"


def test_get_by_name_absent_returns_none():
    repo = _repo_with_memory_db()
    assert repo.get_by_name(env="pre", config_name="nope") is None


def test_env_isolation_same_name_coexist():
    repo = _repo_with_memory_db()
    repo.upsert(
        config_name="x", config_type="single_string", config_value='"pre_v"', env="pre"
    )
    repo.upsert(
        config_name="x", config_type="single_string", config_value='"prod_v"', env="prod"
    )
    assert repo.get_by_name(env="pre", config_name="x").config_value == '"pre_v"'
    assert repo.get_by_name(env="prod", config_name="x").config_value == '"prod_v"'
