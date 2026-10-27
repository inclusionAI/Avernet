"""SQLite harness for artifact dual-write tests(镜像 tests/community/repository/task
的 in-memory DatabasePlugin stub——写点测试在 core 侧,复用同一套 ORM 语义)。

同时再导出框架 fixtures(``world`` / ``community_world``),供本目录的对象存储
实现契约套件使用(pytest 发现 conftest 导入的 fixture 名)。"""
from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from agentclaw.community.core.base import Base
# Side-effect import: registers the task models on Base.metadata.
import agentclaw.community.core.task.repository.models  # noqa: F401

# Re-export framework fixtures for the object-storage contract suite here
# (mirrors tests/community/contracts/conftest.py; pytest discovers imported
# fixture names through any conftest on the collection path). ``world`` depends
# on ``app_with_testing_modules``, so both must be re-exported by their real
# names for pytest to find them.
from tests.community.framework.fixtures import (  # noqa: F401
    app_with_testing_modules,
    world,
)
from tests.community.framework.world import World  # noqa: F401


@pytest.fixture(scope="module")
def community_world() -> World:
    """Module-scoped :class:`World` over the **community** profile injector —
    mirrors ``tests/community/contracts/conftest.py``'s fixture of the same
    name, pinned locally so the moved suite keeps its column assertions."""
    from agentclaw.community.di import DeployProfile, build_injector

    return World(build_injector(profile=DeployProfile.COMMUNITY))


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
