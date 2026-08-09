"""Unified UserMCPConfig repository — behavior + cross-backend contract.

Round-2 spec criteria for this entity: single body subclassing the
UserMCPConfigRepository Protocol, JSON (extra_config/custom_headers)
round-trip, env / env-NULL lookup, auto-save (verified against the real
SqliteDB.orm_session). No ZDAS-skipped test (zero skips policy); prod
round-trip is the manual Pre acceptance gate.
"""
from contextlib import contextmanager

import pytest
from sqlalchemy import Column, MetaData, Table, create_engine
from sqlalchemy.orm import sessionmaker

from agentclaw.community.core.repository.implementations.bot.user_mcp_config import UserMCPConfigRepository

pytestmark = pytest.mark.integration


def _create_schema(engine):
    """ac_user_mcp_config from a private MetaData copy of the model's
    columns — order-independent vs Base-patching tests (pilot precedent)."""
    from agentclaw.community.core.models.mcp import UserMCPConfig

    src = UserMCPConfig.__table__
    md = MetaData()
    Table(
        src.name,
        md,
        *[
            Column(
                c.name,
                c.type,
                primary_key=c.primary_key,
                nullable=c.nullable,
                autoincrement=c.autoincrement,
                server_default=c.server_default.arg
                if c.server_default is not None
                else None,
            )
            for c in src.columns
        ],
    )
    md.create_all(engine)


class _FileSqliteDB:
    def __init__(self, engine):
        self._factory = sessionmaker(
            bind=engine, autocommit=False, autoflush=False
        )

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

    session = orm_session


def _make_db(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'mcp.db'}",
        connect_args={"check_same_thread": False},
    )
    _create_schema(engine)
    return _FileSqliteDB(engine)


@pytest.fixture
def repo(tmp_path):
    return UserMCPConfigRepository(_make_db(tmp_path))


def _cfg(**over):
    base = dict(
        user_id="u1",
        server_code="mcp.test",
        api_key="k1",
        extra_config={"a": 1, "u": "你好"},
        env="dev",
    )
    base.update(over)
    return base


def test_create_returns_dict_with_json_round_trip(repo):
    rec = repo.create(_cfg())
    assert rec["id"] is not None
    assert rec["user_id"] == "u1"
    assert rec["extra_config"] == {"a": 1, "u": "你好"}


def test_create_auto_saves_without_explicit_commit(tmp_path):
    db = _make_db(tmp_path)
    UserMCPConfigRepository(db).create(_cfg(user_id="persist"))
    got = UserMCPConfigRepository(db).get_by_user_and_server_code(
        "persist", "mcp.test"
    )
    assert got is not None and got["user_id"] == "persist"


def test_get_by_id_and_missing(repo):
    rec = repo.create(_cfg())
    assert repo.get_by_id(rec["id"])["server_code"] == "mcp.test"
    assert repo.get_by_id("999999") is None


def test_get_by_user_and_server_code_strict_env(repo):
    # Prod parity: strict env == current; no env-IS-NULL fallback.
    repo.create(_cfg(user_id="a", server_code="s1", env="dev"))
    repo.create(_cfg(user_id="b", server_code="s2", env=None))
    assert repo.get_by_user_and_server_code("a", "s1") is not None
    assert repo.get_by_user_and_server_code("b", "s2") is None


def test_list_by_user(repo):
    repo.create(_cfg(user_id="multi", server_code="s1"))
    repo.create(_cfg(user_id="multi", server_code="s2"))
    repo.create(_cfg(user_id="other", server_code="s3"))
    listed = repo.list_by_user("multi")
    assert {c["server_code"] for c in listed} == {"s1", "s2"}


def test_update_json_fields_and_missing(repo):
    rec = repo.create(_cfg())
    updated = repo.update(
        rec["id"], {"api_key": "k2", "extra_config": {"b": 2}}
    )
    assert updated["api_key"] == "k2"
    assert updated["extra_config"] == {"b": 2}
    assert repo.update("999999", {"api_key": "x"}) is None


def test_delete(repo):
    rec = repo.create(_cfg())
    assert repo.delete(rec["id"]) is True
    assert repo.get_by_id(rec["id"]) is None
    assert repo.delete("999999") is False


def test_real_sqlitedb_orm_session_commits(tmp_path, monkeypatch):
    import importlib

    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'real.db'}")
    db_mod = importlib.import_module("agentclaw.community.plugins.local.database")
    db_mod._engine = None
    db_mod._session_factory = None
    try:
        db_mod._get_session_factory()
        _create_schema(db_mod._engine)
        sqlite_db = db_mod.SqliteDB()
        rec = UserMCPConfigRepository(sqlite_db).create(
            _cfg(user_id="real")
        )
        UserMCPConfigRepository(sqlite_db).update(
            rec["id"], {"extra_config": {"z": 9}}
        )
        got = UserMCPConfigRepository(sqlite_db).get_by_id(rec["id"])
        assert got is not None and got["extra_config"] == {"z": 9}
    finally:
        db_mod._engine = None
        db_mod._session_factory = None


def test_contract_local_backend(tmp_path):
    repo = UserMCPConfigRepository(_make_db(tmp_path))
    created = repo.create(_cfg(user_id="contract"))
    assert set(created) >= {
        "id", "user_id", "server_code", "api_key", "custom_headers",
        "extra_config", "env", "gmt_created", "gmt_modified",
    }
    assert repo.delete(created["id"]) is True


# NOTE: no ZDAS/prod contract test — would be a permanently-skipped test
# (no live OceanBase in CI), which is not allowed. The single unified
# body is exercised in CI by the local leg + real-SqliteDB test; the ZDAS
# round-trip is the manual Pre acceptance gate (round-2 tasks.md Task 7).
