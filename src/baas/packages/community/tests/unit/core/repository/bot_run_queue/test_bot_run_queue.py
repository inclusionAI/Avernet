"""OrmBotRunQueueRepository 真实 SQLite 测试（阶段一，双表）。

直接用 SqliteOrmPlugin 构造内存 SQLite，真实执行 SQL，
验证认领原子性、FIFO、宕机恢复、终态标记、背压计数、BCN 去重等队列语义。
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta
from uuid import uuid4

import pytest

# 导入 ORM 模型以填充 Base.metadata，供 create_all 建表。
import secbaas.core.repository.bot_run_queue._orm_model  # noqa: F401
from secbaas.core.database import DatabaseManager
from secbaas.core.repository.bot_run_queue import OrmBotRunQueueRepository
from secbaas.core.repository.bot_run_queue._orm_model import BotRunQueueModel
from secbaas.plugins.database.stub.sqlite_orm import SqliteOrmPlugin


@pytest.fixture
def db() -> DatabaseManager:
    """全新的内存 SQLite DatabaseManager（每个用例独立、干净）。"""
    plugin = SqliteOrmPlugin("sqlite:///:memory:")
    plugin.create_all()
    mgr = DatabaseManager()
    mgr._sync_session_factory = plugin._sync_session_factory
    mgr._sync_engine = plugin._sync_engine
    return mgr


@pytest.fixture
def repo(db: DatabaseManager) -> OrmBotRunQueueRepository:
    return OrmBotRunQueueRepository(database=db)


def _insert(repo: OrmBotRunQueueRepository, bot_id: str, **kw) -> str:
    run_id = uuid4().hex
    repo.insert_queue(
        run_id=run_id,
        bot_id=bot_id,
        session_id=kw.get("session_id"),
    )
    return run_id


def _force_heartbeat(db: DatabaseManager, run_id: str, when: datetime) -> None:
    with db.orm_session() as s:
        s.query(BotRunQueueModel).filter(BotRunQueueModel.run_id == run_id).update(
            {"last_heartbeat": when}, synchronize_session=False
        )


# ---------------------------------------------------------------------------
# insert_queue 持久化 session_id
# ---------------------------------------------------------------------------


def test_insert_persists_session_id(repo: OrmBotRunQueueRepository):
    run_id = _insert(repo, "bot-1", session_id="sess-9")
    rec = repo.get_by_run_id(run_id)
    assert rec is not None
    assert rec.session_id == "sess-9"
    assert rec.status == "PENDING"
    assert rec.assigned_worker is None
    assert rec.last_heartbeat is None
    assert rec.meta == {}


def test_insert_without_session_id_defaults_none(repo: OrmBotRunQueueRepository):
    run_id = _insert(repo, "bot-1")
    rec = repo.get_by_run_id(run_id)
    assert rec is not None and rec.session_id is None


def test_insert_with_meta(repo: OrmBotRunQueueRepository):
    run_id = uuid4().hex
    repo.insert_queue(
        run_id=run_id,
        bot_id="bot-1",
        meta={"callback_function": "bcn_uplink", "foo": "bar"},
    )
    rec = repo.get_by_run_id(run_id)
    assert rec is not None
    assert rec.meta.get("callback_function") == "bcn_uplink"
    assert rec.meta.get("foo") == "bar"


# ---------------------------------------------------------------------------
# claim_pending_by_bot —— 行级出队原子性 + FIFO
# ---------------------------------------------------------------------------


def test_claim_sets_running_and_worker(repo: OrmBotRunQueueRepository):
    run_id = _insert(repo, "bot-1")
    claimed = repo.claim_pending_by_bot("bot-1", "worker-A")
    assert claimed is not None
    assert claimed.run_id == run_id
    assert claimed.status == "RUNNING"
    assert claimed.assigned_worker == "worker-A"
    assert claimed.last_heartbeat is not None


def test_claim_is_fifo_by_gmt_create(repo: OrmBotRunQueueRepository):
    first = _insert(repo, "bot-1")
    time.sleep(0.01)
    second = _insert(repo, "bot-1")
    assert repo.claim_pending_by_bot("bot-1", "w").run_id == first
    assert repo.claim_pending_by_bot("bot-1", "w").run_id == second


def test_claim_each_row_only_once(repo: OrmBotRunQueueRepository):
    _insert(repo, "bot-1")
    a = repo.claim_pending_by_bot("bot-1", "worker-A")
    b = repo.claim_pending_by_bot("bot-1", "worker-B")
    assert a is not None
    assert b is None  # 已无 PENDING，第二个 Worker 捞空


def test_claim_distinct_rows_across_workers(repo: OrmBotRunQueueRepository):
    ids = {_insert(repo, "bot-1") for _ in range(3)}
    claimed = {repo.claim_pending_by_bot("bot-1", f"w{i}").run_id for i in range(3)}
    assert claimed == ids
    assert repo.claim_pending_by_bot("bot-1", "w-extra") is None


def test_claim_returns_none_when_empty(repo: OrmBotRunQueueRepository):
    assert repo.claim_pending_by_bot("bot-none", "w") is None


def test_claim_isolated_per_bot(repo: OrmBotRunQueueRepository):
    _insert(repo, "bot-1")
    assert repo.claim_pending_by_bot("bot-2", "w") is None


# ---------------------------------------------------------------------------
# discover_active_bots
# ---------------------------------------------------------------------------


def test_discover_active_bots_distinct_sorted(repo: OrmBotRunQueueRepository):
    _insert(repo, "bot-b")
    _insert(repo, "bot-b")
    _insert(repo, "bot-a")
    assert repo.discover_active_bots() == ["bot-a", "bot-b"]


def test_discover_excludes_running(repo: OrmBotRunQueueRepository):
    _insert(repo, "bot-1")
    repo.claim_pending_by_bot("bot-1", "w")
    assert repo.discover_active_bots() == []


# ---------------------------------------------------------------------------
# 宕机恢复 reset_stale_running / release_to_pending / touch_heartbeat / mark_done
# ---------------------------------------------------------------------------


def test_reset_stale_running_resets_old_heartbeat(
    repo: OrmBotRunQueueRepository, db: DatabaseManager
):
    run_id = _insert(repo, "bot-1")
    repo.claim_pending_by_bot("bot-1", "dead-worker")
    _force_heartbeat(db, run_id, datetime.now() - timedelta(seconds=600))

    reset = repo.reset_stale_running(stale_seconds=150)
    assert reset == 1
    rec = repo.get_by_run_id(run_id)
    assert rec.status == "PENDING"
    assert rec.assigned_worker is None
    assert rec.last_heartbeat is None


def test_reset_stale_running_keeps_fresh(
    repo: OrmBotRunQueueRepository, db: DatabaseManager
):
    _insert(repo, "bot-1")
    repo.claim_pending_by_bot("bot-1", "alive-worker")  # heartbeat ~ now
    assert repo.reset_stale_running(stale_seconds=150) == 0


def test_touch_heartbeat_refreshes(repo: OrmBotRunQueueRepository, db: DatabaseManager):
    run_id = _insert(repo, "bot-1")
    repo.claim_pending_by_bot("bot-1", "w")
    _force_heartbeat(db, run_id, datetime.now() - timedelta(seconds=600))
    repo.touch_heartbeat(run_id)
    assert repo.reset_stale_running(stale_seconds=150) == 0


def test_release_to_pending(repo: OrmBotRunQueueRepository):
    run_id = _insert(repo, "bot-1")
    repo.claim_pending_by_bot("bot-1", "w")
    assert repo.release_to_pending(run_id) == 1
    rec = repo.get_by_run_id(run_id)
    assert rec.status == "PENDING"
    assert rec.assigned_worker is None
    assert repo.release_to_pending(run_id) == 0


def test_mark_done(repo: OrmBotRunQueueRepository):
    run_id = _insert(repo, "bot-1")
    repo.claim_pending_by_bot("bot-1", "w")
    assert repo.mark_done(run_id) == 1
    assert repo.get_by_run_id(run_id).status == "DONE"
    # 已 DONE 再次 mark 无效果
    assert repo.mark_done(run_id) == 0


def test_mark_done_not_discoverable(repo: OrmBotRunQueueRepository):
    run_id = _insert(repo, "bot-1")
    repo.claim_pending_by_bot("bot-1", "w")
    repo.mark_done(run_id)
    # DONE 行不被发现 / 不被认领
    assert repo.discover_active_bots() == []
    assert repo.claim_pending_by_bot("bot-1", "w") is None


# ---------------------------------------------------------------------------
# count_pending_by_bot / update_meta
# ---------------------------------------------------------------------------


def test_count_pending_by_bot(repo: OrmBotRunQueueRepository):
    _insert(repo, "bot-1")
    _insert(repo, "bot-1")
    claimed = _insert(repo, "bot-1")
    repo.claim_pending_by_bot("bot-1", "w")  # 认领最早一条
    assert repo.count_pending_by_bot("bot-1") == 2
    assert repo.count_pending_by_bot("bot-other") == 0
    _ = claimed


def test_update_meta_merges_json(repo: OrmBotRunQueueRepository):
    run_id = _insert(repo, "bot-1")
    assert repo.update_meta(run_id, {"bcn_callback_sent": True}) is True
    assert repo.get_by_run_id(run_id).meta.get("bcn_callback_sent") is True
    # 再次合并，保留已有字段
    assert repo.update_meta(run_id, {"extra": 42}) is True
    rec = repo.get_by_run_id(run_id)
    assert rec.meta.get("bcn_callback_sent") is True
    assert rec.meta.get("extra") == 42
    # 不存在的 run_id
    assert repo.update_meta("nonexistent", {}) is False
