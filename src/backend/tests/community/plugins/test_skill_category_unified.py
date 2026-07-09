"""Unified SkillCategory repository — behavior + cross-backend contract.

Round-2 spec criteria for this entity: single body, 7-method CRUD parity,
auto-save (verified against the real SqliteDB.orm_session), descendant
listing. No ZDAS-skipped test (zero skips policy); prod round-trip is the
manual Pre acceptance gate.
"""
from contextlib import contextmanager

import pytest
from sqlalchemy import Column, MetaData, Table, create_engine
from sqlalchemy.orm import sessionmaker

from agentclaw.community.plugins.skill_category_repository import (
    SkillCategoryRepository,
)

pytestmark = pytest.mark.integration


def _create_schema(engine):
    """ac_skill_category from a private MetaData copy of the model's
    columns — order-independent vs Base-patching tests (pilot precedent)."""
    from agentclaw.community.core.models.skill import SkillCategory

    src = SkillCategory.__table__
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
        f"sqlite:///{tmp_path / 'cat.db'}",
        connect_args={"check_same_thread": False},
    )
    _create_schema(engine)
    return _FileSqliteDB(engine)


@pytest.fixture
def repo(tmp_path):
    return SkillCategoryRepository(_make_db(tmp_path))


def test_create_returns_dict_and_get_by_code_path(repo):
    rec = repo.create("biz", "Business", "", "/biz/", 0, 1)
    assert rec["id"] is not None
    assert rec["code"] == "biz"
    assert repo.get_by_code("biz")["path"] == "/biz/"
    assert repo.get_by_path("/biz/")["name"] == "Business"
    assert repo.get_by_code("nope") is None


def test_create_auto_saves_without_explicit_commit(tmp_path):
    db = _make_db(tmp_path)
    SkillCategoryRepository(db).create("p", "P", "", "/p/", 0, 0)
    assert SkillCategoryRepository(db).get_by_code("p") is not None


def test_list_active_orders_by_level_then_sort(repo):
    repo.create("a", "A", "", "/a/", 1, 2)
    repo.create("b", "B", "", "/b/", 0, 5)
    repo.create("c", "C", "", "/c/", 1, 1)
    codes = [r["code"] for r in repo.list_active()]
    assert codes == ["b", "c", "a"]


def test_update_by_code_and_by_path(repo):
    repo.create("x", "X", "", "/x/", 0, 0)
    repo.update("x", name="X2")
    assert repo.get_by_code("x")["name"] == "X2"
    repo.update_by_path("/x/", sort_order=9)
    assert repo.get_by_path("/x/")["sort_order"] == 9


def test_update_missing_returns_none(repo):
    assert repo.update("missing", name="z") is None
    assert repo.update_by_path("/missing/", name="z") is None


def test_list_descendant_codes(repo):
    repo.create("root", "R", "", "/root/", 0, 0)
    repo.create("child", "C", "root", "/root/child/", 1, 0)
    repo.create("other", "O", "", "/other/", 0, 0)
    desc = set(repo.list_descendant_codes("/root/"))
    assert desc == {"root", "child"}


def test_list_descendant_excludes_disabled(repo):
    repo.create("r", "R", "", "/r/", 0, 0)
    repo.create("d", "D", "r", "/r/d/", 1, 0)
    repo.update_by_path("/r/d/", status=0)
    assert repo.list_descendant_codes("/r/") == ["r"]


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
        SkillCategoryRepository(sqlite_db).create(
            "real", "Real", "", "/real/", 0, 0
        )
        got = SkillCategoryRepository(sqlite_db).get_by_code("real")
        assert got is not None and got["path"] == "/real/"
    finally:
        db_mod._engine = None
        db_mod._session_factory = None


def test_contract_local_backend(tmp_path):
    repo = SkillCategoryRepository(_make_db(tmp_path))
    created = repo.create("contract", "C", "", "/contract/", 0, 0)
    assert set(created) >= {
        "id", "code", "name", "parent_code", "path", "level",
        "sort_order", "status", "gmt_created", "gmt_modified",
    }
    repo.update("contract", name="Renamed")
    assert repo.get_by_code("contract")["name"] == "Renamed"


# NOTE: no ZDAS/prod contract test — would be a permanently-skipped test
# (no live OceanBase in CI), which is not allowed. The single unified
# body is exercised in CI by the local leg + real-SqliteDB test; the ZDAS
# round-trip is the manual Pre acceptance gate (round-2 tasks.md Task 7).
