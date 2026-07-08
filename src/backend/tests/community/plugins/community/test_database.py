"""Unit tests for the community CommunityDatabase (B3).

CommunityDatabase is a pure connection provider — it does NOT create tables, so
these tests provision the schema themselves (as a community operator would).
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
    # Operator-provisioned schema — the impl does not create tables.
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

