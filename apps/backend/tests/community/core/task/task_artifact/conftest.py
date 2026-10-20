"""SQLite harness for artifact dual-write tests(镜像 tests/community/repository/task
的 in-memory DatabasePlugin stub——写点测试在 core 侧,复用同一套 ORM 语义)。"""
from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from agentclaw.community.core.base import Base
# Side-effect import: registers the task models on Base.metadata.
import agentclaw.community.core.task.repository.models  # noqa: F401


class InMemorySqliteDB:
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
