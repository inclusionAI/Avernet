"""OrmBotQpmRepository 真实 SQLite 测试（阶段一）。"""

from __future__ import annotations

import pytest

# 导入 ORM 模型以填充 Base.metadata，供 create_all 建表。
import secbaas.community.core.repository.bot_qpm._orm_model  # noqa: F401
from secbaas.community.core.database import DatabaseManager
from secbaas.community.core.repository.bot_qpm import OrmBotQpmRepository
from secbaas.community.plugins.database.sqlite.sqlite_orm import SqliteOrmPlugin


@pytest.fixture
def db() -> DatabaseManager:
    plugin = SqliteOrmPlugin("sqlite:///:memory:")
    plugin.create_all()
    mgr = DatabaseManager()
    mgr._sync_session_factory = plugin._sync_session_factory
    mgr._sync_engine = plugin._sync_engine
    return mgr


@pytest.fixture
def repo(db: DatabaseManager) -> OrmBotQpmRepository:
    return OrmBotQpmRepository(database=db)


def test_get_missing_returns_none(repo: OrmBotQpmRepository):
    assert repo.get_by_bot_id("bot-x") is None


def test_upsert_inserts_then_reads(repo: OrmBotQpmRepository):
    repo.upsert(bot_id="bot-1", qpm=120)
    rec = repo.get_by_bot_id("bot-1")
    assert rec is not None
    assert rec.bot_id == "bot-1"
    assert rec.qpm == 120


def test_upsert_updates_existing(repo: OrmBotQpmRepository):
    repo.upsert(bot_id="bot-1", qpm=120)
    repo.upsert(bot_id="bot-1", qpm=30)
    rec = repo.get_by_bot_id("bot-1")
    assert rec.qpm == 30
    # 不应产生重复行
    assert len(repo.list_all()) == 1


def test_list_all(repo: OrmBotQpmRepository):
    repo.upsert(bot_id="bot-1", qpm=60)
    repo.upsert(bot_id="bot-2", qpm=200)
    by_bot = {r.bot_id: r.qpm for r in repo.list_all()}
    assert by_bot == {"bot-1": 60, "bot-2": 200}
