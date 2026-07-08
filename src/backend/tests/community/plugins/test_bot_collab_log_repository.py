"""Unit tests for BotCollabLogRepository.

Tests the repository behavior with in-memory SQLite.
"""
import pytest
from contextlib import contextmanager
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agentclaw.community.plugins.bot_collab_log_repository import (
    BotCollabLogRepository,
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
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    # Create tables
    from agentclaw.community.core.base import Base
    Base.metadata.create_all(engine)
    db = InMemorySqliteDB(engine)
    return BotCollabLogRepository(db)


# --- insert tests -----------------------------------------------------------

def test_insert_returns_record_with_id(repo, monkeypatch):
    """Test that insert creates a record with generated ID."""
    monkeypatch.setenv("SERVER_ENV", "dev")
    record = repo.insert({
        "bot_id": "bot-001",
        "owner_id": "user-001",
        "detail": "Added collaborator user-002",
        "operator_id": "user-001",
    })
    assert record.id is not None
    assert record.bot_id == "bot-001"
    assert record.owner_id == "user-001"
    assert record.detail == "Added collaborator user-002"
    assert record.operator_id == "user-001"


def test_insert_persists_to_database(repo, monkeypatch):
    """Test that insert actually persists data."""
    monkeypatch.setenv("SERVER_ENV", "dev")
    repo.insert({
        "bot_id": "bot-001",
        "owner_id": "user-001",
        "detail": "Test log",
        "operator_id": "user-001",
    })
    # Query with a new repo instance to verify persistence
    records = repo.list_by_bot("bot-001", "user-001")
    assert len(records) == 1
    assert records[0].detail == "Test log"


def test_insert_auto_saves_without_explicit_commit(repo, monkeypatch):
    """Test that orm_session auto-commits on clean exit."""
    monkeypatch.setenv("SERVER_ENV", "dev")
    repo.insert({
        "bot_id": "bot-001",
        "owner_id": "user-001",
        "detail": "Auto-save test",
        "operator_id": "user-001",
    })
    # Data should be persisted without explicit commit
    records = repo.list_by_bot("bot-001", "user-001")
    assert len(records) == 1


# --- list_by_bot tests ------------------------------------------------------

def test_list_by_bot_returns_records_for_matching_bot(repo, monkeypatch):
    """Test that list_by_bot returns logs for specific bot."""
    monkeypatch.setenv("SERVER_ENV", "dev")
    repo.insert({
        "bot_id": "bot-001",
        "owner_id": "user-001",
        "detail": "Log 1",
        "operator_id": "user-001",
    })
    repo.insert({
        "bot_id": "bot-001",
        "owner_id": "user-001",
        "detail": "Log 2",
        "operator_id": "user-002",
    })
    repo.insert({
        "bot_id": "bot-002",
        "owner_id": "user-001",
        "detail": "Log 3",
        "operator_id": "user-001",
    })

    records = repo.list_by_bot("bot-001", "user-001")
    assert len(records) == 2
    details = {r.detail for r in records}
    assert details == {"Log 1", "Log 2"}


def test_list_by_bot_returns_empty_for_no_matches(repo, monkeypatch):
    """Test that list_by_bot returns empty list when no matches."""
    monkeypatch.setenv("SERVER_ENV", "dev")
    repo.insert({
        "bot_id": "bot-001",
        "owner_id": "user-001",
        "detail": "Log",
        "operator_id": "user-001",
    })

    records = repo.list_by_bot("bot-999", "user-001")
    assert records == []


def test_list_by_bot_respects_limit(repo, monkeypatch):
    """Test that list_by_bot respects limit parameter."""
    monkeypatch.setenv("SERVER_ENV", "dev")
    for i in range(10):
        repo.insert({
            "bot_id": "bot-001",
            "owner_id": "user-001",
            "detail": f"Log {i}",
            "operator_id": "user-001",
        })

    records = repo.list_by_bot("bot-001", "user-001", limit=5)
    assert len(records) == 5


def test_list_by_bot_returns_descending_order(repo, monkeypatch):
    """Test that list_by_bot returns records in descending order by gmt_create."""
    import time
    monkeypatch.setenv("SERVER_ENV", "dev")
    repo.insert({
        "bot_id": "bot-001",
        "owner_id": "user-001",
        "detail": "First log",
        "operator_id": "user-001",
    })
    time.sleep(0.1)  # Ensure different timestamps (SQLite has low timestamp precision)
    repo.insert({
        "bot_id": "bot-001",
        "owner_id": "user-001",
        "detail": "Second log",
        "operator_id": "user-001",
    })

    records = repo.list_by_bot("bot-001", "user-001")
    assert records[0].detail == "Second log"
    assert records[1].detail == "First log"


# --- list_by_operator tests -------------------------------------------------

def test_list_by_operator_returns_records_for_operator(repo, monkeypatch):
    """Test that list_by_operator returns logs for specific operator."""
    monkeypatch.setenv("SERVER_ENV", "dev")
    repo.insert({
        "bot_id": "bot-001",
        "owner_id": "user-001",
        "detail": "Log 1",
        "operator_id": "user-001",
    })
    repo.insert({
        "bot_id": "bot-002",
        "owner_id": "user-002",
        "detail": "Log 2",
        "operator_id": "user-001",
    })
    repo.insert({
        "bot_id": "bot-001",
        "owner_id": "user-001",
        "detail": "Log 3",
        "operator_id": "user-002",
    })

    records = repo.list_by_operator("user-001")
    assert len(records) == 2
    details = {r.detail for r in records}
    assert details == {"Log 1", "Log 2"}


def test_list_by_operator_respects_limit(repo, monkeypatch):
    """Test that list_by_operator respects limit parameter."""
    monkeypatch.setenv("SERVER_ENV", "dev")
    for i in range(10):
        repo.insert({
            "bot_id": "bot-001",
            "owner_id": "user-001",
            "detail": f"Log {i}",
            "operator_id": "user-001",
        })

    records = repo.list_by_operator("user-001", limit=3)
    assert len(records) == 3