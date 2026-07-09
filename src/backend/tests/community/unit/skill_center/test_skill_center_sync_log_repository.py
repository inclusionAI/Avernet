import pytest
from contextlib import contextmanager
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agentclaw.community.plugins.skill_center_sync_log_repository import (
    SkillCenterSyncLogRepository,
)


class InMemorySqliteDB:
    """In-memory SQLite DB for unit testing."""

    def __init__(self, engine):
        self._engine = engine
        self._session_factory = sessionmaker(bind=self._engine, autoflush=False)

    @contextmanager
    def session(self):
        db = self._session_factory()
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    # Unified repo uses orm_session(); mirror session() (commit on clean exit).
    orm_session = session


@pytest.fixture
def repo(tmp_path):
    db_path = str(tmp_path / "sync_test.db")
    # Create tables
    from agentclaw.community.core.base import Base
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    # Use file-based sqlite for test
    db = InMemorySqliteDB(engine)
    return SkillCenterSyncLogRepository(db)


def test_create_returns_record_with_id(repo):
    record = repo.create({
        "skill_uuid": "uuid-B",
        "version": "1.0.0",
        "env": "dev",
        "status": "pending",
    })
    assert record["skill_uuid"] == "uuid-B"
    assert record.get("id") is not None


def test_mark_success_updates_record(repo):
    _ = repo.create({
        "skill_uuid": "uuid-C",
        "version": "2.0.0",
        "env": "dev",
        "status": "pending",
    })
    repo.mark_success("uuid-C", "2.0.0", "dev", checksum="abc123")
    updated = repo.find_latest("uuid-C", "dev")
    assert updated["status"] == "success"
    assert updated["checksum"] == "abc123"


def test_mark_failed_updates_error_msg(repo):
    _ = repo.create({
        "skill_uuid": "uuid-D",
        "version": "1.0.0",
        "env": "dev",
        "status": "pending",
    })
    repo.mark_failed("uuid-D", "1.0.0", "dev", error_msg="download failed")
    updated = repo.find_latest("uuid-D", "dev")
    assert updated["status"] == "failed"
    assert "download failed" in updated["error_msg"]


def test_find_latest_returns_most_recent(repo):
    repo.create({"skill_uuid": "uuid-E", "version": "1.0.0", "env": "dev", "status": "pending"})
    repo.create({"skill_uuid": "uuid-E", "version": "2.0.0", "env": "dev", "status": "pending"})
    latest = repo.find_latest("uuid-E", "dev")
    assert latest["version"] == "2.0.0"
