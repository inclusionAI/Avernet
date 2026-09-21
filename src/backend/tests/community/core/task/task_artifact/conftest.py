"""SQLite harness for task_artifact service tests.

Mirrors ``tests/community/repository/task/conftest.py`` (real in-memory SQLite,
``Base.metadata.create_all``, minimal orm_session with commit/rollback semantics
identical to the prod DatabasePlugin)."""
from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from agentclaw.community.core.base import Base
# Side-effect import: registers the task models (incl. task_artifact) on Base.metadata.
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