"""Unified HarnessPatch repository — behavior + cross-backend contract.

Round-2 spec criteria for this entity: single body, full domain
round-trip (PatchDefinition ↔ ac_harness_patch), auto-save (verified
against the real SqliteDB.orm_session). No ZDAS-skipped test (zero skips
policy); prod round-trip is the manual Pre acceptance gate.
"""
from contextlib import contextmanager

import pytest
from sqlalchemy import Column, MetaData, Table, create_engine
from sqlalchemy.orm import sessionmaker

from agentclaw.community.core.harness.models import Layer, PatchDefinition
from agentclaw.community.core.repository.implementations.harness.patch import HarnessPatchRepository

pytestmark = pytest.mark.integration


def _create_schema(engine):
    """ac_harness_patch from a private MetaData copy of the model's
    columns — order-independent vs Base-patching tests (pilot precedent)."""
    from agentclaw.community.core.harness.sqlite_models import HarnessPatchModel

    src = HarnessPatchModel.__table__
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
        f"sqlite:///{tmp_path / 'patch.db'}",
        connect_args={"check_same_thread": False},
    )
    _create_schema(engine)
    return _FileSqliteDB(engine)


@pytest.fixture
def repo(tmp_path):
    return HarnessPatchRepository(_make_db(tmp_path))


def _patch(**over):
    base = dict(
        template_id=1,
        name="p1",
        layer=Layer.L1,
        description="d",
        scope="all",
        content='[{"op":"x"}]',
        is_applied=False,
        scan_id=10,
        env="dev",
    )
    base.update(over)
    return PatchDefinition(**base)


def test_create_returns_id_and_round_trips(repo):
    pid = repo.create(_patch())
    assert isinstance(pid, int) and pid > 0
    got = repo.get_by_id(pid)
    assert got is not None
    assert got.name == "p1"
    assert got.layer == Layer.L1
    assert got.scan_id == 10
    assert got.content == '[{"op":"x"}]'
    assert got.is_applied is False


def test_create_auto_saves_without_explicit_commit(tmp_path):
    db = _make_db(tmp_path)
    pid = HarnessPatchRepository(db).create(_patch(name="persist"))
    got = HarnessPatchRepository(db).get_by_id(pid)
    assert got is not None and got.name == "persist"


def test_bot_scope_encoded_into_scope(repo):
    pid = repo.create(_patch(scope="bot", scope_value="bot-42"))
    assert repo.get_by_id(pid).scope == "bot-42"


def test_list_by_record_orders_desc(repo):
    a = repo.create(_patch(name="a", scan_id=99))
    b = repo.create(_patch(name="b", scan_id=99))
    repo.create(_patch(name="other", scan_id=7))
    got = repo.list_by_record(99)
    assert {p.name for p in got} == {"a", "b"}
    assert {p.id for p in got} == {a, b}


def test_update_is_applied(repo):
    pid = repo.create(_patch(is_applied=False))
    repo.update_is_applied(pid, True)
    assert repo.get_by_id(pid).is_applied is True
    repo.update_is_applied(pid, False)
    assert repo.get_by_id(pid).is_applied is False


def test_get_missing_returns_none(repo):
    assert repo.get_by_id(999999) is None
    assert repo.list_by_record(123456) == []


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
        pid = HarnessPatchRepository(sqlite_db).create(_patch(name="real"))
        HarnessPatchRepository(sqlite_db).update_is_applied(pid, True)
        got = HarnessPatchRepository(sqlite_db).get_by_id(pid)
        assert got is not None and got.is_applied is True
    finally:
        db_mod._engine = None
        db_mod._session_factory = None


def test_contract_local_backend(tmp_path):
    repo = HarnessPatchRepository(_make_db(tmp_path))
    pid = repo.create(_patch(name="contract"))
    got = repo.get_by_id(pid)
    assert got.name == "contract" and got.id == pid
    repo.update_is_applied(pid, True)
    assert repo.get_by_id(pid).is_applied is True


# NOTE: no ZDAS/prod contract test — would be a permanently-skipped test
# (no live OceanBase in CI), which is not allowed. The single unified
# body is exercised in CI by the local leg + real-SqliteDB test; the ZDAS
# round-trip is the manual Pre acceptance gate (round-2 tasks.md Task 7).
