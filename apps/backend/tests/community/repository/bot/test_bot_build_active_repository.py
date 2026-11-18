"""Unit tests for BotBuildActiveRepository.

The same single ORM body runs on prod OceanBase, so the UNIQUE presence marker,
token-fenced clear/touch, TTL-expiry semantics and heartbeat renewal are
exercised against a real in-memory SQLite database.
"""
from contextlib import contextmanager
from datetime import timedelta

import pytest
from sqlalchemy import DateTime, create_engine, func, select, type_coerce
from sqlalchemy.orm import sessionmaker

from agentclaw.community.core.repository.implementations.bot.build_active import (
    BotBuildActiveRepository,
)
# Imported for side effect: registers BotBuildActiveModel on Base.metadata
# so create_all() builds the ac_bot_build_active table.
from agentclaw.community.core.bot_management.repository.models import (  # noqa: F401
    BotBuildActiveModel,
)


class InMemorySqliteDB:
    """In-memory SQLite DB for unit testing."""

    def __init__(self, engine):
        self._engine = engine
        self._session_factory = sessionmaker(bind=self._engine, autoflush=False)

    @contextmanager
    def orm_session(self):
        db = self._session_factory()
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()


@pytest.fixture
def repo_and_engine():
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}
    )
    from agentclaw.community.core.base import Base
    Base.metadata.create_all(engine)
    return BotBuildActiveRepository(InMemorySqliteDB(engine)), engine


@pytest.fixture
def repo(repo_and_engine):
    return repo_and_engine[0]


def _age_row(engine, *, entity_id: str, bot_id: str, age: timedelta) -> None:
    """Backdate a marker row to simulate a build that crashed long ago.

    The marker's ``gmt_create`` comes from the DB clock (``func.now()``), so
    age it against that same clock rather than the test process's local time.
    """
    session = sessionmaker(bind=engine)()
    try:
        row = (
            session.query(BotBuildActiveModel)
            .filter_by(env="dev", entity_id=entity_id, bot_id=bot_id)
            .one()
        )
        db_now = session.execute(select(type_coerce(func.now(), DateTime))).scalar()
        row.gmt_create = db_now - age
        row.gmt_modified = row.gmt_create
        session.commit()
    finally:
        session.close()


def _row_count(engine) -> int:
    session = sessionmaker(bind=engine)()
    try:
        return session.query(BotBuildActiveModel).count()
    finally:
        session.close()


# --- mark / is_active -------------------------------------------------------

def test_mark_active_returns_token_and_is_active(repo):
    token = repo.mark_active("dev", "ent_a", "bot_1")
    assert token
    assert repo.is_active("dev", "ent_a", "bot_1") is True


def test_is_active_false_when_absent(repo):
    assert repo.is_active("dev", "ent_missing", "bot_missing") is False


def test_second_mark_returns_none_and_keeps_owner_row(repo):
    first = repo.mark_active("dev", "ent_b", "bot_2")
    assert first
    # A second build of the same Bot sees the UNIQUE conflict instead of
    # taking over the marker; only the original owner clears it.
    assert repo.mark_active("dev", "ent_b", "bot_2") is None
    assert repo.is_active("dev", "ent_b", "bot_2") is True


def test_marker_is_scoped_per_bot(repo):
    assert repo.mark_active("dev", "ent_a", "bot_1")
    assert repo.mark_active("dev", "ent_a", "bot_other")
    assert repo.is_active("dev", "ent_a", "bot_1") is True
    assert repo.is_active("dev", "ent_a", "bot_other") is True


# --- clear (token-fenced) ---------------------------------------------------

def test_clear_requires_matching_token(repo):
    token = repo.mark_active("dev", "ent_c", "bot_3")
    assert token
    # A stale owner must not delete the current marker.
    assert repo.clear("dev", "ent_c", "bot_3", "not-the-token") is False
    assert repo.is_active("dev", "ent_c", "bot_3") is True
    # The owner clears it.
    assert repo.clear("dev", "ent_c", "bot_3", token) is True
    assert repo.is_active("dev", "ent_c", "bot_3") is False
    # Clearing again is a harmless no-op.
    assert repo.clear("dev", "ent_c", "bot_3", token) is False


def test_old_owner_cannot_clear_its_successors_marker(repo_and_engine):
    """F2: a slow build whose marker was replaced must not delete the new one."""
    repo, engine = repo_and_engine
    old_token = repo.mark_active("dev", "ent_f", "bot_6")
    assert old_token
    # Simulate the old build outliving the TTL: the row expires and a new build
    # takes the key with its own token.
    _age_row(engine, entity_id="ent_f", bot_id="bot_6", age=timedelta(hours=1))
    new_token = repo.mark_active("dev", "ent_f", "bot_6")
    assert new_token and new_token != old_token

    assert repo.clear("dev", "ent_f", "bot_6", old_token) is False
    assert repo.is_active("dev", "ent_f", "bot_6") is True
    assert repo.clear("dev", "ent_f", "bot_6", new_token) is True


# --- touch (heartbeat) ------------------------------------------------------

def test_touch_renews_an_expired_marker_for_its_owner(repo_and_engine):
    repo, engine = repo_and_engine
    token = repo.mark_active("dev", "ent_g", "bot_7")
    assert token
    _age_row(engine, entity_id="ent_g", bot_id="bot_7", age=timedelta(hours=1))
    assert repo.is_active("dev", "ent_g", "bot_7") is False

    # The heartbeat renews the marker from the DB clock, restoring protection.
    assert repo.touch("dev", "ent_g", "bot_7", token) is True
    assert repo.is_active("dev", "ent_g", "bot_7") is True


def test_touch_rejects_a_foreign_token(repo):
    token = repo.mark_active("dev", "ent_h", "bot_8")
    assert token
    assert repo.touch("dev", "ent_h", "bot_8", "not-the-token") is False
    assert repo.touch("dev", "ent_h", "bot_8", token) is True


# --- TTL --------------------------------------------------------------------

def test_zero_ttl_treats_fresh_row_as_expired(repo):
    assert repo.mark_active("dev", "ent_d", "bot_4")
    assert repo.is_active("dev", "ent_d", "bot_4", ttl_seconds=0) is False


def test_mark_replaces_crashed_build_marker(repo_and_engine):
    repo, engine = repo_and_engine
    assert repo.mark_active("dev", "ent_e", "bot_5")
    _age_row(engine, entity_id="ent_e", bot_id="bot_5", age=timedelta(hours=1))

    # The crashed build's marker is expired, so a new build can claim the key
    # instead of being locked out by the UNIQUE constraint forever.
    assert repo.mark_active("dev", "ent_e", "bot_5")
    assert repo.is_active("dev", "ent_e", "bot_5") is True
    assert _row_count(engine) == 1