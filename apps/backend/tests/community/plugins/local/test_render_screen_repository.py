"""Integration tests for the unified RenderScreenRepository — real in-memory SQLite."""
import pytest
from unittest.mock import patch
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from agentclaw.community.plugin_api.models import Base


@pytest.fixture
def session_factory():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    from agentclaw.community.core.bot_management.render_screen.sqlite_models import RenderScreenModel  # noqa: F401
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    yield Session
    Base.metadata.drop_all(bind=engine)
    engine.dispose()


@pytest.fixture
def mock_db(session_factory):
    from contextlib import contextmanager

    class MockDB:
        # Unified repo uses orm_session(): commit on clean exit,
        # rollback on exception (the SqliteDB.orm_session contract).
        @contextmanager
        def orm_session(self):
            db = session_factory()
            try:
                yield db
                db.commit()
            except Exception:
                db.rollback()
                raise
            finally:
                db.close()

        session = orm_session

    return MockDB()


@pytest.fixture
def repo(mock_db):
    from agentclaw.community.core.repository.implementations.bot.render_screen import RenderScreenRepository

    with patch(
        "agentclaw.community.core.repository.implementations.bot.render_screen.get_current_env",
        return_value="dev",
    ):
        yield RenderScreenRepository(mock_db)


def _insert(repo, **overrides):
    defaults = dict(
        bot_id="bot_001",
        owner_id="user_001",
        name="数据看板",
        cdn_url="https://cdn.example.com/v1/index.js",
        creator_id="user_001",
    )
    defaults.update(overrides)
    return repo.insert(**defaults)


class TestInsert:
    def test_insert_returns_id(self, repo):
        rid = _insert(repo)
        assert isinstance(rid, int) and rid > 0

    def test_insert_persists_fields(self, repo):
        rid = _insert(repo, name="图表面板", cdn_url="https://cdn.example.com/v2/main.js")
        rec = repo.get_by_id(rid)
        assert rec is not None
        assert rec.bot_id == "bot_001"
        assert rec.owner_id == "user_001"
        assert rec.name == "图表面板"
        assert rec.cdn_url == "https://cdn.example.com/v2/main.js"
        assert rec.creator_id == "user_001"
        assert rec.is_delete == 0
        assert rec.gmt_create is not None
        assert rec.gmt_modified is not None


class TestInsertDuplicate:
    def test_repo_allows_duplicates(self, repo):
        """Prod ac_bot_render_screen has no (bot_id,name,env) unique
        index, so the repo must NOT reject duplicates — dedup is the
        service layer's responsibility. Both inserts succeed."""
        id1 = _insert(repo, bot_id="bot_dup", name="重复看板")
        id2 = _insert(repo, bot_id="bot_dup", name="重复看板")
        assert id1 != id2


class TestListByBotId:
    def test_returns_records_for_bot(self, repo):
        _insert(repo, bot_id="bot_001", name="看板1")
        _insert(repo, bot_id="bot_001", name="看板2")
        _insert(repo, bot_id="bot_002", name="看板3")
        rows = repo.list_by_bot_id(bot_id="bot_001", owner_id="user_001")
        assert len(rows) == 2
        assert all(r.bot_id == "bot_001" for r in rows)

    def test_excludes_deleted(self, repo):
        rid = _insert(repo, bot_id="bot_001")
        repo.delete_by_id(record_id=rid)
        rows = repo.list_by_bot_id(bot_id="bot_001", owner_id="user_001")
        assert rows == []

    def test_returns_empty(self, repo):
        rows = repo.list_by_bot_id(bot_id="nonexistent", owner_id="user_001")
        assert rows == []

    def test_default_bot_isolates_by_owner_id(self, repo):
        """不同用户共享 bot_id='default' 时，按 owner_id 隔离查询结果。"""
        _insert(repo, bot_id="default", owner_id="user_A", name="看板A")
        _insert(repo, bot_id="default", owner_id="user_B", name="看板B")
        rows_a = repo.list_by_bot_id(bot_id="default", owner_id="user_A")
        rows_b = repo.list_by_bot_id(bot_id="default", owner_id="user_B")
        assert len(rows_a) == 1
        assert rows_a[0].name == "看板A"
        assert len(rows_b) == 1
        assert rows_b[0].name == "看板B"


class TestGetById:
    def test_returns_record(self, repo):
        rid = _insert(repo)
        rec = repo.get_by_id(rid)
        assert rec is not None
        assert rec.id == rid

    def test_returns_none_for_missing(self, repo):
        assert repo.get_by_id(99999) is None

    def test_excludes_deleted(self, repo):
        rid = _insert(repo)
        repo.delete_by_id(record_id=rid)
        assert repo.get_by_id(rid) is None


class TestUpdateById:
    def test_update_name_and_url(self, repo):
        rid = _insert(repo, name="旧名称", cdn_url="https://old.url")
        repo.update_by_id(record_id=rid, name="新名称", cdn_url="https://new.url")
        rec = repo.get_by_id(rid)
        assert rec.name == "新名称"
        assert rec.cdn_url == "https://new.url"

    def test_update_does_not_affect_deleted(self, repo):
        rid = _insert(repo, name="旧名称")
        repo.delete_by_id(record_id=rid)
        repo.update_by_id(record_id=rid, name="新名称", cdn_url="https://new.url")
        assert repo.get_by_id(rid) is None


class TestDeleteById:
    def test_soft_delete(self, repo):
        rid = _insert(repo)
        repo.delete_by_id(record_id=rid)
        assert repo.list_by_bot_id(bot_id="bot_001", owner_id="user_001") == []
        assert repo.get_by_id(rid) is None
