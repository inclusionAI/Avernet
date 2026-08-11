"""ResultGuardExecutor 单元测试。

ResultGuardExecutor 是 executor 链的业务结果兜底层：Worker 不直接写
``baas_bot_run``，这里只验证 FAILED 终态归属在 executor 内。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from uuid import uuid4

import pytest

from secbaas.community.core.database import DatabaseManager
from secbaas.community.core.repository.bot_run import OrmBotRunRepository
from secbaas.community.core.repository.bot_run_queue import BotRunQueueRecord
from secbaas.community.core.service.bot_run._executor import (
    RequeuedToPendingError,
    ResultGuardExecutor,
)
from secbaas.community.plugins.database.sqlite.sqlite_orm import SqliteOrmPlugin


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
def repo(db: DatabaseManager) -> OrmBotRunRepository:
    return OrmBotRunRepository(database=db)


class _RaisingInner:
    async def execute(self, record: BotRunQueueRecord) -> None:
        raise RuntimeError("boom")


class _TimeoutInner:
    async def execute(self, record: BotRunQueueRecord) -> None:
        raise TimeoutError("inner timeout")


class _RequeueInner:
    async def execute(self, record: BotRunQueueRecord) -> None:
        raise RequeuedToPendingError(record.run_id, "sess-1")


class _RecordingInner:
    def __init__(self) -> None:
        self.executed: list[str] = []

    async def execute(self, record: BotRunQueueRecord) -> None:
        self.executed.append(record.run_id)


def _insert_run(repo: OrmBotRunRepository) -> str:
    run_id = uuid4().hex
    repo.insert_run(
        run_id=run_id,
        bot_id="bot-1",
        api_key_prefix="sk-",
        message_long="m",
        metadata=None,
    )
    return run_id


def _record(run_id: str, *, meta: dict | None = None) -> BotRunQueueRecord:
    return BotRunQueueRecord(
        id=1,
        gmt_create=datetime.now(),
        gmt_modified=None,
        run_id=run_id,
        bot_id="bot-1",
        session_id="sess-1",
        status="RUNNING",
        assigned_worker="worker-1",
        last_heartbeat=None,
        meta=meta or {},
        env="dev",
    )


async def test_marks_failed_when_inner_raises(repo: OrmBotRunRepository):
    run_id = _insert_run(repo)
    ex = ResultGuardExecutor(_RaisingInner(), repo)

    await ex.execute(_record(run_id))

    rec = repo.get_by_run_id(run_id)
    assert rec.status == "FAILED"
    assert "boom" in (rec.error or "")


async def test_requeue_signal_is_propagated_and_not_marked_failed(
    repo: OrmBotRunRepository,
):
    run_id = _insert_run(repo)
    ex = ResultGuardExecutor(_RequeueInner(), repo)

    with pytest.raises(RequeuedToPendingError):
        await ex.execute(_record(run_id))

    assert repo.get_by_run_id(run_id).status == "PENDING"


async def test_pre_execute_timeout_marks_failed_without_running_inner(
    repo: OrmBotRunRepository,
):
    run_id = _insert_run(repo)
    inner = _RecordingInner()
    ex = ResultGuardExecutor(inner, repo)
    rec = _record(run_id, meta={"timeout": 1})
    rec.gmt_create = datetime.now() - timedelta(seconds=2)

    await ex.execute(rec)

    assert inner.executed == []
    assert repo.get_by_run_id(run_id).status == "FAILED"


async def test_timeout_error_inner_marks_failed(repo: OrmBotRunRepository):
    run_id = _insert_run(repo)
    ex = ResultGuardExecutor(_TimeoutInner(), repo)

    await ex.execute(_record(run_id))

    rec = repo.get_by_run_id(run_id)
    assert rec.status == "FAILED"
    assert "Task execution timeout" in (rec.error or "")
