"""Unit tests for BotRestartLockRepository.

Tests the repository behavior with in-memory SQLite — the same single ORM
body that runs on prod OceanBase, so the UNIQUE guard, token-fenced release,
and DB-side staleness are exercised against a real database.
"""
import pytest
from contextlib import contextmanager
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agentclaw.community.plugins.bot_restart_lock_repository import (
    BotRestartLockRepository,
)
# Imported for side effect: registers BotRestartLockModel on Base.metadata
# so create_all() builds the ac_bot_restart_lock table.
from agentclaw.community.core.bot_management.repository.models import (  # noqa: F401
    BotRestartLockModel,
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
def repo():
    """Create a repository with in-memory SQLite database."""
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}
    )
    from agentclaw.community.core.base import Base
    Base.metadata.create_all(engine)
    db = InMemorySqliteDB(engine)
    return BotRestartLockRepository(db)


# --- acquire / UNIQUE guard -------------------------------------------------

def test_acquire_persists_and_returns_record(repo):
    rec = repo.acquire("dev", "ent_a", "bot_c1", "u1")
    assert rec is not None
    assert rec.id is not None
    assert rec.lock_token  # a fencing token was minted
    # Visible via an independent read.
    assert repo.get("dev", "ent_a", "bot_c1") is not None


def test_acquire_returns_none_on_duplicate(repo):
    first = repo.acquire("dev", "ent_b", "bot_c2", "u1")
    assert first is not None
    # Duplicate INSERT on the same (env, entity_id, bot_id) is swallowed as
    # None — the UNIQUE constraint is the guard.
    assert repo.acquire("dev", "ent_b", "bot_c2", "u2") is None
    # The conflict must not disturb the original row — proves the None came
    # from a UNIQUE conflict, not a destructive delete-then-failed-reinsert.
    surviving = repo.get("dev", "ent_b", "bot_c2")
    assert surviving is not None
    assert surviving.holder_user_id == "u1"


def test_unique_guard_scoped_per_bot(repo):
    assert repo.acquire("dev", "ent_a", "bot_c1", "u1") is not None
    # A different bot_id is an independent key.
    assert repo.acquire("dev", "ent_a", "bot_c1b", "u1") is not None


# --- release (token-fenced compare-and-delete) ------------------------------

def test_release_requires_matching_token(repo):
    rec = repo.acquire("dev", "ent_d", "bot_c4", "u1")
    assert rec is not None
    # Wrong token must not delete the row.
    assert repo.release("dev", "ent_d", "bot_c4", "not-the-token") is False
    assert repo.get("dev", "ent_d", "bot_c4") is not None
    # Correct token deletes it.
    assert repo.release("dev", "ent_d", "bot_c4", rec.lock_token) is True
    assert repo.get("dev", "ent_d", "bot_c4") is None


def test_release_frees_key_for_reacquire(repo):
    first = repo.acquire("dev", "ent_a", "bot_c1", "u1")
    assert repo.release("dev", "ent_a", "bot_c1", first.lock_token) is True
    # A subsequent acquire then succeeds again.
    assert repo.acquire("dev", "ent_a", "bot_c1", "u2") is not None


def test_late_release_does_not_delete_newer_lock(repo):
    """The fencing scenario: an old holder's late release must not delete the
    lock a different holder acquired after the old one was reaped."""
    old = repo.acquire("dev", "ent_e", "bot_c5", "u1")
    # Simulate the reaper deleting the old (stale) row.
    assert repo.release("dev", "ent_e", "bot_c5", old.lock_token) is True
    # A different holder acquires a fresh lock on the same key.
    new = repo.acquire("dev", "ent_e", "bot_c5", "u2")
    assert new is not None and new.lock_token != old.lock_token
    # The old holder's late release (its finally) must NOT delete the new lock.
    assert repo.release("dev", "ent_e", "bot_c5", old.lock_token) is False
    survivor = repo.get("dev", "ent_e", "bot_c5")
    assert survivor is not None
    assert survivor.lock_token == new.lock_token


# --- get / get_if_stale -----------------------------------------------------

def test_get_returns_none_when_absent(repo):
    assert repo.get("dev", "ent_x", "missing") is None


def test_get_if_stale_judges_db_side(repo):
    assert repo.acquire("dev", "ent_c", "bot_c3", "u1") is not None
    # Just-created row is not stale under a generous TTL.
    assert repo.get_if_stale("dev", "ent_c", "bot_c3", 3600) is None
    # With a zero TTL, any existing row is stale (elapsed >= 0).
    assert repo.get_if_stale("dev", "ent_c", "bot_c3", 0) is not None
    # Absent row → None regardless of TTL.
    assert repo.get_if_stale("dev", "ent_c", "missing", 0) is None
