"""Unit tests for the community CommunityDatabase (B3).

CommunityDatabase owns the real schema by default: ``bootstrap()`` runs
``core/schema.py``'s ``create_all`` unless ``create_schema=False``. The
session-level tests below are about connection and transaction behaviour rather
than the app schema, so they provision their own throwaway tables from a
private ``Base`` — ``bootstrap()`` would emit the *real* metadata, which these
tests do not need. The bootstrap wiring itself is covered separately at the
bottom of this file.

Tests run against a temp on-disk SQLite file (a real persistent store, unlike
the in-memory local impl).
"""
from __future__ import annotations

import pytest
from sqlalchemy import Column, ForeignKey, Integer, String
from sqlalchemy.orm import declarative_base

from agentclaw.community.plugins.community.database import CommunityDatabase

Base = declarative_base()


class _Parent(Base):
    __tablename__ = "b3_parent"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)


class _Child(Base):
    __tablename__ = "b3_child"
    id = Column(Integer, primary_key=True)
    parent_id = Column(
        Integer, ForeignKey("b3_parent.id", ondelete="CASCADE"), nullable=False
    )


@pytest.fixture
def db(tmp_path) -> CommunityDatabase:
    url = f"sqlite:///{tmp_path}/b3.db"
    database = CommunityDatabase(url)
    # These tests exercise session/transaction behaviour against throwaway
    # tables, so the private Base is provisioned directly instead of going
    # through bootstrap() (which would emit the real application schema).
    Base.metadata.create_all(database._engine)
    return database


def test_orm_session_persists_on_clean_exit(db):
    with db.orm_session() as s:
        s.add(_Parent(id=1, name="alice"))
    # New session in the same process sees the committed row.
    with db.session() as s:
        assert s.query(_Parent).filter_by(id=1).one().name == "alice"


def test_session_does_not_auto_commit(db):
    with db.session() as s:
        s.add(_Parent(id=2, name="bob"))
        # no commit
    with db.session() as s:
        assert s.query(_Parent).filter_by(id=2).first() is None


def test_orm_session_rolls_back_on_exception(db):
    with pytest.raises(RuntimeError):
        with db.orm_session() as s:
            s.add(_Parent(id=3, name="carol"))
            raise RuntimeError("boom")
    with db.session() as s:
        assert s.query(_Parent).filter_by(id=3).first() is None


def test_persists_across_sessions_within_process(db):
    with db.orm_session() as s:
        s.add(_Parent(id=4, name="dave"))
    with db.orm_session() as s:
        s.add(_Child(id=40, parent_id=4))
    with db.session() as s:
        assert s.query(_Child).filter_by(id=40).one().parent_id == 4


def test_session_rolls_back_on_exception(db):
    with pytest.raises(RuntimeError):
        with db.session() as s:
            s.add(_Parent(id=9, name="frank"))
            raise RuntimeError("boom")
    with db.session() as s:
        assert s.query(_Parent).filter_by(id=9).first() is None


def test_non_sqlite_url_uses_plain_engine(monkeypatch):
    # A non-SQLite URL takes the plain create_engine branch with NO SQLite-only
    # connect_args / FK pragma. Patch create_engine so the test needs no DB
    # driver (real create_engine eagerly imports the dialect's driver).
    import agentclaw.community.plugins.community.database as dbmod

    seen: dict = {}

    def fake_create_engine(url, **kwargs):
        seen["url"] = url
        seen["kwargs"] = kwargs
        return object()  # dummy engine; sessionmaker only stores the bind

    monkeypatch.setattr(dbmod, "create_engine", fake_create_engine)
    CommunityDatabase("postgresql://user:pw@localhost/agentclaw")
    assert seen["url"] == "postgresql://user:pw@localhost/agentclaw"
    assert "connect_args" not in seen["kwargs"]  # SQLite-only, not applied here


def test_sqlite_foreign_key_cascade_fires(db):
    # Insert in dependency order (no ORM relationship is declared, so a
    # single-session flush would not order parent-before-child for us).
    with db.orm_session() as s:
        s.add(_Parent(id=5, name="erin"))
    with db.orm_session() as s:
        s.add(_Child(id=50, parent_id=5))
    # PRAGMA foreign_keys=ON → deleting the parent cascades to the child.
    with db.orm_session() as s:
        parent = s.query(_Parent).filter_by(id=5).one()
        s.delete(parent)
    with db.session() as s:
        assert s.query(_Child).filter_by(id=50).first() is None



class TestBootstrapSchemaOwnership:
    """``bootstrap()`` is what makes a container deployment self-provisioning.

    The DDL itself lives in ``core/schema.py`` and is tested there; what matters
    here is the wiring — that the plugin calls it at all, honours
    ``create_schema``, and passes the dialect flag matching its own URL.
    """

    @staticmethod
    def _record_create_all(monkeypatch) -> list[dict]:
        """Capture core.schema.create_all calls without emitting real DDL."""
        calls: list[dict] = []

        def _fake_create_all(engine, *, mysql: bool = False) -> None:
            calls.append({"engine": engine, "mysql": mysql})

        monkeypatch.setattr(
            "agentclaw.community.core.schema.create_all", _fake_create_all
        )
        return calls

    @pytest.mark.asyncio
    async def test_creates_the_schema_by_default(self, tmp_path, monkeypatch):
        calls = self._record_create_all(monkeypatch)
        database = CommunityDatabase(f"sqlite:///{tmp_path}/boot.db")

        await database.bootstrap()

        assert len(calls) == 1
        assert calls[0]["engine"] is database._engine

    @pytest.mark.asyncio
    async def test_skips_ddl_when_the_operator_owns_the_schema(
        self, tmp_path, monkeypatch
    ):
        calls = self._record_create_all(monkeypatch)
        database = CommunityDatabase(
            f"sqlite:///{tmp_path}/boot.db", create_schema=False
        )

        await database.bootstrap()

        assert calls == []

    @pytest.mark.asyncio
    async def test_sqlite_url_does_not_request_the_mysql_ddl_pass(
        self, tmp_path, monkeypatch
    ):
        calls = self._record_create_all(monkeypatch)
        database = CommunityDatabase(f"sqlite:///{tmp_path}/boot.db")

        await database.bootstrap()

        assert calls[0]["mysql"] is False

    @pytest.mark.asyncio
    async def test_mysql_url_requests_the_mysql_ddl_pass(self, monkeypatch):
        # create_engine is lazy, so this needs no reachable server. Without the
        # flag the index keys would exceed InnoDB's cap and CREATE INDEX fails.
        calls = self._record_create_all(monkeypatch)
        database = CommunityDatabase(
            "mysql+pymysql://u:p@db.example:3306/agentclaw?charset=utf8mb4"
        )

        await database.bootstrap()

        assert calls[0]["mysql"] is True
