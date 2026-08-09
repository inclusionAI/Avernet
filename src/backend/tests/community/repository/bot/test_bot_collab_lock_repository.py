"""Unit tests for BotCollabLockRepository.

Tests the repository behavior with in-memory SQLite.
"""
import pytest
from contextlib import contextmanager
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.exc import IntegrityError

from agentclaw.community.core.repository.implementations.bot.collab_lock import BotCollabLockRepository
from agentclaw.community.core.bot_collaborator.models import BotCollabLockModel


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
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    # Create tables
    from agentclaw.community.core.base import Base
    Base.metadata.create_all(engine)
    db = InMemorySqliteDB(engine)
    return BotCollabLockRepository(db)


# --- acquire tests ----------------------------------------------------------

def test_acquire_returns_record_with_id(repo, monkeypatch):
    """Test that acquire creates a lock with generated ID."""
    monkeypatch.setenv("SERVER_ENV", "dev")
    record = repo.acquire("lock-key-001", "user-001")
    assert record.id is not None
    assert record.lock_key == "lock-key-001"
    assert record.holder_user_id == "user-001"
    assert record.env == "dev"


def test_acquire_persists_to_database(repo, monkeypatch):
    """Test that acquire actually persists lock."""
    monkeypatch.setenv("SERVER_ENV", "dev")
    repo.acquire("lock-key-001", "user-001")
    found = repo.get_by_key("lock-key-001")
    assert found is not None
    assert found.holder_user_id == "user-001"


def test_acquire_raises_on_duplicate_lock_key(repo, monkeypatch):
    """Test that acquire raises IntegrityError for duplicate lock_key+env."""
    monkeypatch.setenv("SERVER_ENV", "dev")
    repo.acquire("lock-key-001", "user-001")
    with pytest.raises(IntegrityError):
        repo.acquire("lock-key-001", "user-002")


def test_acquire_auto_saves_without_explicit_commit(repo, monkeypatch):
    """Test that orm_session auto-commits on clean exit."""
    monkeypatch.setenv("SERVER_ENV", "dev")
    repo.acquire("lock-key-001", "user-001")
    # Data should be persisted without explicit commit
    assert repo.is_locked("lock-key-001")


# --- get_by_key tests -------------------------------------------------------

def test_get_by_key_returns_lock_when_exists(repo, monkeypatch):
    """Test that get_by_key returns lock record."""
    monkeypatch.setenv("SERVER_ENV", "dev")
    repo.acquire("lock-key-001", "user-001")
    record = repo.get_by_key("lock-key-001")
    assert record is not None
    assert record.lock_key == "lock-key-001"
    assert record.holder_user_id == "user-001"


def test_get_by_key_returns_none_when_not_exists(repo, monkeypatch):
    """Test that get_by_key returns None for non-existent lock."""
    monkeypatch.setenv("SERVER_ENV", "dev")
    record = repo.get_by_key("non-existent")
    assert record is None


# --- is_locked tests --------------------------------------------------------

def test_is_locked_returns_true_when_locked(repo, monkeypatch):
    """Test that is_locked returns True for existing lock."""
    monkeypatch.setenv("SERVER_ENV", "dev")
    repo.acquire("lock-key-001", "user-001")
    assert repo.is_locked("lock-key-001") is True


def test_is_locked_returns_false_when_not_locked(repo, monkeypatch):
    """Test that is_locked returns False for non-existent lock."""
    monkeypatch.setenv("SERVER_ENV", "dev")
    assert repo.is_locked("non-existent") is False


# --- list_by_holder tests ---------------------------------------------------

def test_list_by_holder_returns_locks_for_user(repo, monkeypatch):
    """Test that list_by_holder returns all locks held by user."""
    monkeypatch.setenv("SERVER_ENV", "dev")
    repo.acquire("lock-key-001", "user-001")
    repo.acquire("lock-key-002", "user-001")
    repo.acquire("lock-key-003", "user-002")

    locks = repo.list_by_holder("user-001")
    assert len(locks) == 2
    lock_keys = {l.lock_key for l in locks}
    assert lock_keys == {"lock-key-001", "lock-key-002"}


def test_list_by_holder_returns_empty_for_no_locks(repo, monkeypatch):
    """Test that list_by_holder returns empty list when user has no locks."""
    monkeypatch.setenv("SERVER_ENV", "dev")
    locks = repo.list_by_holder("user-001")
    assert locks == []


def test_list_by_holder_returns_descending_order(repo, monkeypatch):
    """Test that list_by_holder returns records in descending order by gmt_create."""
    import time
    monkeypatch.setenv("SERVER_ENV", "dev")
    repo.acquire("lock-key-001", "user-001")
    time.sleep(1.1)  # SQLite CURRENT_TIMESTAMP has only second precision
    repo.acquire("lock-key-002", "user-001")

    locks = repo.list_by_holder("user-001")
    assert locks[0].lock_key == "lock-key-002"
    assert locks[1].lock_key == "lock-key-001"


# --- release tests ----------------------------------------------------------

def test_release_removes_lock(repo, monkeypatch):
    """Test that release removes existing lock."""
    monkeypatch.setenv("SERVER_ENV", "dev")
    repo.acquire("lock-key-001", "user-001")
    assert repo.is_locked("lock-key-001") is True

    result = repo.release("lock-key-001")
    assert result is True
    assert repo.is_locked("lock-key-001") is False


def test_release_returns_false_for_non_existent(repo, monkeypatch):
    """Test that release returns False for non-existent lock."""
    monkeypatch.setenv("SERVER_ENV", "dev")
    result = repo.release("non-existent")
    assert result is False


def test_release_can_re_acquire_after_release(repo, monkeypatch):
    """Test that same lock can be re-acquired after release."""
    monkeypatch.setenv("SERVER_ENV", "dev")
    repo.acquire("lock-key-001", "user-001")
    repo.release("lock-key-001")

    # Should not raise IntegrityError
    record = repo.acquire("lock-key-001", "user-002")
    assert record.holder_user_id == "user-002"


# --- integration tests ------------------------------------------------------

def test_full_lock_lifecycle(repo, monkeypatch):
    """Test complete lock lifecycle: acquire -> check -> release -> verify."""
    monkeypatch.setenv("SERVER_ENV", "dev")
    lock_key = "test-lock-001"
    user = "user-001"

    # Initially not locked
    assert not repo.is_locked(lock_key)

    # Acquire lock
    lock = repo.acquire(lock_key, user)
    assert lock.lock_key == lock_key
    assert lock.holder_user_id == user

    # Verify locked
    assert repo.is_locked(lock_key)

    # Verify holder
    found = repo.get_by_key(lock_key)
    assert found.holder_user_id == user

    # List by holder
    locks = repo.list_by_holder(user)
    assert len(locks) == 1
    assert locks[0].lock_key == lock_key

    # Release lock
    assert repo.release(lock_key)

    # Verify released
    assert not repo.is_locked(lock_key)
    assert repo.get_by_key(lock_key) is None
    assert repo.list_by_holder(user) == []