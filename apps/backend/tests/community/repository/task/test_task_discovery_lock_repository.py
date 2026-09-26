"""Unit tests for TaskDiscoveryLockRepository.

Tests the repository behavior with in-memory SQLite — the same single ORM
body that runs on prod OceanBase, so the UNIQUE guard, token-fenced release,
and DB-side staleness are exercised against a real database.

Follows the same pattern as test_bot_restart_lock_repository.py.
"""
import pytest
from contextlib import contextmanager
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agentclaw.community.core.repository.implementations.task.discovery_lock import (
    TaskDiscoveryLockRepository,
)
# Imported for side effect: registers TaskDiscoveryLockModel on Base.metadata
# so create_all() builds the ac_task_discovery_lock table.
from agentclaw.community.core.task.task_discovery.lock_models import (  # noqa: F401
    TaskDiscoveryLockModel,
    TaskDiscoveryLockRecord,
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
    return TaskDiscoveryLockRepository(db)


# --- acquire / UNIQUE guard -------------------------------------------------

def test_acquire_persists_and_returns_record(repo):
    rec = repo.acquire("dev", "bot_c1", "2026-08-25", "host-1")
    assert rec is not None
    assert rec.id is not None
    assert rec.lock_token  # a fencing token was minted
    assert rec.env == "dev"
    assert rec.bot_id == "bot_c1"
    assert rec.discovery_date == "2026-08-25"
    assert rec.holder == "host-1"


def test_acquire_returns_none_on_duplicate(repo):
    first = repo.acquire("dev", "bot_c2", "2026-08-25", "host-1")
    assert first is not None
    # Duplicate INSERT on the same (env, bot_id, discovery_date) is swallowed
    # as None — the UNIQUE constraint is the guard.
    assert repo.acquire("dev", "bot_c2", "2026-08-25", "host-2") is None


def test_acquire_different_dates_both_succeed(repo):
    """Lock key includes discovery_date — different dates are not a conflict."""
    r1 = repo.acquire("dev", "bot_c3", "2026-08-25", "host-1")
    r2 = repo.acquire("dev", "bot_c3", "2026-08-26", "host-2")
    assert r1 is not None
    assert r2 is not None


# --- release / token fence --------------------------------------------------

def test_release_deletes_matching_token(repo):
    rec = repo.acquire("dev", "bot_r1", "2026-08-25", "host-1")
    assert rec is not None
    assert repo.release("dev", "bot_r1", "2026-08-25", rec.lock_token) is True
    # After release, a new acquire for the same key should succeed.
    rec2 = repo.acquire("dev", "bot_r1", "2026-08-25", "host-2")
    assert rec2 is not None


def test_release_wrong_token_is_noop(repo):
    rec = repo.acquire("dev", "bot_r2", "2026-08-25", "host-1")
    assert rec is not None
    # Wrong token → no delete, returns False.
    assert repo.release("dev", "bot_r2", "2026-08-25", "wrong-token") is False
    # Lock is still held — a new acquire still fails.
    assert repo.acquire("dev", "bot_r2", "2026-08-25", "host-2") is None


def test_release_nonexistent_key_is_noop(repo):
    assert repo.release("dev", "nope", "2026-08-25", "any-token") is False


# --- get_if_stale / TTL -----------------------------------------------------

def test_get_if_stale_returns_none_when_no_row(repo):
    assert repo.get_if_stale("dev", "bot_s1", "2026-08-25", 600) is None


def test_get_if_stale_returns_none_when_fresh(repo):
    """A freshly acquired lock is not stale."""
    repo.acquire("dev", "bot_s2", "2026-08-25", "host-1")
    # TTL of 0 seconds — everything is stale immediately, but even a
    # generous TTL should return None for a just-created row on a fast
    # in-memory DB. Use a very large TTL to confirm freshness.
    assert repo.get_if_stale("dev", "bot_s2", "2026-08-25", 999999) is None


def test_get_if_stale_returns_record_when_expired(repo):
    """A lock older than the TTL is stale and returned for reaping."""
    repo.acquire("dev", "bot_s3", "2026-08-25", "host-1")
    # TTL of 0 seconds — the lock is immediately stale.
    stale = repo.get_if_stale("dev", "bot_s3", "2026-08-25", 0)
    assert stale is not None
    assert stale.bot_id == "bot_s3"


# --- _as_naive helper ------------------------------------------------------

def test_as_naive_strips_tzinfo():
    """_as_naive drops tzinfo so two DB-clock timestamps can be subtracted."""
    from datetime import datetime, timezone
    from agentclaw.community.core.repository.implementations.task.discovery_lock import (
        _as_naive,
    )
    aware = datetime(2026, 8, 25, 12, 0, 0, tzinfo=timezone.utc)
    naive = _as_naive(aware)
    assert naive.tzinfo is None
    # Already-naive passthrough.
    bare = datetime(2026, 8, 25, 12, 0, 0)
    assert _as_naive(bare) is bare


# --- to_record --------------------------------------------------------------

def test_to_record_round_trip(repo):
    """to_record() converts the ORM model to the dataclass record."""
    from agentclaw.community.core.task.task_discovery.lock_models import (
        TaskDiscoveryLockRecord,
    )
    rec = repo.acquire("dev", "bot_tr", "2026-08-25", "host-1")
    assert isinstance(rec, TaskDiscoveryLockRecord)
    assert rec.env == "dev"
    assert rec.bot_id == "bot_tr"
    assert rec.discovery_date == "2026-08-25"
    assert rec.holder == "host-1"
    assert rec.lock_token
    assert rec.gmt_create is not None
