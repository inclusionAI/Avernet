"""Shared SQLite harness for task repository tests.

Mirrors tests/community/repository/platform/test_task_queue_repository.py: a real
in-memory SQLite engine, Base.metadata.create_all, and a minimal orm_session
stub that commits on clean exit / rolls back on error — same semantics the prod
DatabasePlugin gives, so the single ORM body behaves identically here."""
from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from agentclaw.community.core.base import Base
# Side-effect import: registers the 5 task models on Base.metadata.
import agentclaw.community.core.task.repository.models  # noqa: F401


class InMemorySqliteDB:
    """Minimal DatabasePlugin stand-in offering orm_session()."""

    def __init__(self, engine):
        self._factory = sessionmaker(bind=engine, autoflush=False)

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


@pytest.fixture
def engine():
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(eng)
    return eng


@pytest.fixture
def db(engine):
    return InMemorySqliteDB(engine)
