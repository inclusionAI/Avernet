"""SerializingExecutor 单元测试（阶段一,增量 4；双表）。

用假锁服务 + 真实内存 SQLite 队列仓库，验证 session 串行语义：
- 无 session_id：直接执行内层（首条消息）。
- 锁空闲：抢到锁 → 执行内层。
- 锁被占：不执行内层 → 队列工作项放回 PENDING（串行,不丢）。
- 内层抛异常：锁仍释放，异常上抛给 Worker 兜底。
"""

from __future__ import annotations

from contextlib import contextmanager
from uuid import uuid4

import pytest

from secbaas.core.database import DatabaseManager
from secbaas.core.repository.bot_run_queue import (
    BotRunQueueRecord,
    OrmBotRunQueueRepository,
)
from secbaas.core.service.bot_run._executor import (
    RequeuedToPendingError,
    SerializingExecutor,
)
from secbaas.plugins.database.stub.sqlite_orm import SqliteOrmPlugin


@pytest.fixture(autouse=True)
def _mock_db_manager():
    """覆盖 service 层 conftest 的 autouse DB mock，本模块用真实 SQLite。"""
    yield


@pytest.fixture
def db() -> DatabaseManager:
    plugin = SqliteOrmPlugin("sqlite:///:memory:")
    plugin.create_all()
    mgr = DatabaseManager()
    orig_factory = mgr._sync_session_factory
    orig_engine = mgr._sync_engine
    mgr._sync_session_factory = plugin._sync_session_factory
    mgr._sync_engine = plugin._sync_engine
    try:
        yield mgr
    finally:
        mgr._sync_session_factory = orig_factory
        mgr._sync_engine = orig_engine
        plugin._sync_engine.dispose()


@pytest.fixture
def queue(db: DatabaseManager) -> OrmBotRunQueueRepository:
    return OrmBotRunQueueRepository(database=db)


# ----------------------------- fakes -----------------------------


class _FakeLock:
    def __init__(self, acquired: bool):
        self.acquired = acquired


class _FakeLockService:
    """可控的假锁：未被占则获取成功，进入/退出维护占用集合。"""

    def __init__(self):
        self._held: set[str] = set()
        self.acquire_calls: list[str] = []

    def force_hold(self, lock_name: str) -> None:
        self._held.add(lock_name)

    @contextmanager
    def try_lock(
        self,
        lock_name,
        lock_holder=None,
        expire_seconds=None,
        block=False,
        block_timeout=None,
    ):
        acquired = lock_name not in self._held
        self.acquire_calls.append(lock_name)
        if acquired:
            self._held.add(lock_name)
        try:
            yield _FakeLock(acquired)
        finally:
            if acquired:
                self._held.discard(lock_name)


class _RecordingInner:
    def __init__(self, queue: OrmBotRunQueueRepository, raise_exc: bool = False):
        self._queue = queue
        self._raise = raise_exc
        self.executed: list[str] = []

    async def execute(self, record: BotRunQueueRecord) -> None:
        self.executed.append(record.run_id)
        if self._raise:
            raise RuntimeError("inner boom")
        self._queue.mark_done(record.run_id)


def _claimed(
    queue: OrmBotRunQueueRepository, session_id: str | None
) -> BotRunQueueRecord:
    run_id = uuid4().hex
    queue.insert_queue(run_id=run_id, bot_id="bot-1", session_id=session_id)
    return queue.claim_pending_by_bot("bot-1", "w")  # → RUNNING, session_id 透传


# ----------------------------- tests -----------------------------


async def test_empty_session_runs_inner_without_lock(queue):
    lock = _FakeLockService()
    inner = _RecordingInner(queue)
    ex = SerializingExecutor(inner, lock, queue)

    rec = _claimed(queue, session_id=None)
    await ex.execute(rec)

    assert inner.executed == [rec.run_id]
    assert lock.acquire_calls == []  # 没尝试加锁
    assert queue.get_by_run_id(rec.run_id).status == "DONE"


async def test_free_lock_runs_inner(queue):
    lock = _FakeLockService()
    inner = _RecordingInner(queue)
    ex = SerializingExecutor(inner, lock, queue)

    rec = _claimed(queue, session_id="s1")
    await ex.execute(rec)

    assert inner.executed == [rec.run_id]
    assert lock.acquire_calls == ["botrun:session:bot-1:dev:s1"]
    assert queue.get_by_run_id(rec.run_id).status == "DONE"


async def test_busy_lock_requeues_and_skips_inner(queue):
    lock = _FakeLockService()
    lock.force_hold("botrun:session:bot-1:dev:s1")  # 模拟同 session 有请求在执行
    inner = _RecordingInner(queue)
    ex = SerializingExecutor(inner, lock, queue)

    rec = _claimed(queue, session_id="s1")
    assert queue.get_by_run_id(rec.run_id).status == "RUNNING"

    # 抢不到锁 → 放回 PENDING 并抛 RequeuedToPending，由 Worker 捕获跳过 callback
    with pytest.raises(RequeuedToPendingError, match="requeued"):
        await ex.execute(rec)

    assert inner.executed == []  # 内层未执行
    # 放回 PENDING，等前序完成后重新认领
    after = queue.get_by_run_id(rec.run_id)
    assert after.status == "PENDING"
    assert after.assigned_worker is None


async def test_lock_released_after_execute(queue):
    lock = _FakeLockService()
    inner = _RecordingInner(queue)
    ex = SerializingExecutor(inner, lock, queue)

    rec1 = _claimed(queue, session_id="s1")
    await ex.execute(rec1)
    rec2 = _claimed(queue, session_id="s1")
    await ex.execute(rec2)

    assert inner.executed == [rec1.run_id, rec2.run_id]
    assert queue.get_by_run_id(rec2.run_id).status == "DONE"


async def test_inner_exception_releases_lock_and_propagates(queue):
    lock = _FakeLockService()
    inner = _RecordingInner(queue, raise_exc=True)
    ex = SerializingExecutor(inner, lock, queue)

    rec = _claimed(queue, session_id="s1")
    with pytest.raises(RuntimeError, match="inner boom"):
        await ex.execute(rec)

    # 锁已释放：同 session 下一个能拿到
    assert "botrun:session:bot-1:dev:s1" not in lock._held
