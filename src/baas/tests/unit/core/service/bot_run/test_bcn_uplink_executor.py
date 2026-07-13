"""BcnUplinkCallback 单元测试（阶段一,增量 6；双表）。

用假上行客户端 + 真实内存 SQLite（baas_bot_run 结果），验证 BCN uplink 回调语义：
- 终态（COMPLETED/FAILED）：上报一次。
- 非终态：不上报。
- run 不存在：跳过。
- 上报失败：不抛异常（仅日志）。
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from secbaas.community.core.database import DatabaseManager
from secbaas.community.core.repository.bot_run import OrmBotRunRepository
from secbaas.community.core.repository.bot_run_queue import (
    BotRunQueueRecord,
    OrmBotRunQueueRepository,
)
from secbaas.community.core.service.bcn.uplink import BcnUplinkCallback
from secbaas.community.plugins.database.stub.sqlite_orm import SqliteOrmPlugin


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


@pytest.fixture
def queue(db: DatabaseManager) -> OrmBotRunQueueRepository:
    return OrmBotRunQueueRepository(database=db)


# ----------------------------- fakes -----------------------------


class _FakeUplinkResult:
    def __init__(self, ok: bool = True, deduplicated: bool = False):
        self.ok = ok
        self.deduplicated = deduplicated


class _FakeUplinkClient:
    def __init__(self, raise_exc: bool = False):
        self._raise = raise_exc
        self.calls: list[tuple[str, str]] = []  # (bot_id, event_id)

    async def send_event(self, event, bot_id, event_id=None):
        self.calls.append((bot_id, event_id))
        if self._raise:
            raise RuntimeError("uplink boom")
        return _FakeUplinkResult()


def _make_terminal(
    repo: OrmBotRunRepository,
    queue: OrmBotRunQueueRepository,
    *,
    terminal: str = "COMPLETED",
) -> str:
    """创建 run + queue 记录并将 run 写入指定终态，返回 run_id。"""
    run_id = uuid4().hex
    repo.insert_run(
        run_id=run_id,
        bot_id="bot-1",
        api_key_prefix="sk-",
        message_long="m",
        metadata={},
    )
    queue.insert_queue(run_id=run_id, bot_id="bot-1", meta={"request_type": "chat"})

    if terminal == "COMPLETED":
        repo.update_result(run_id, "answer", {})
    elif terminal == "FAILED":
        repo.update_error(run_id, "boom")
    # RUNNING：什么都不写（保持 PENDING 非终态）

    return run_id


# ----------------------------- tests -----------------------------


async def test_completed_sends_uplink_once(repo, queue):
    client = _FakeUplinkClient()
    cb = BcnUplinkCallback(client, repo)

    run_id = _make_terminal(repo, queue, terminal="COMPLETED")
    await cb(run_id)

    assert client.calls == [("bot-1", run_id)]


async def test_failed_sends_uplink(repo, queue):
    client = _FakeUplinkClient()
    cb = BcnUplinkCallback(client, repo)

    run_id = _make_terminal(repo, queue, terminal="FAILED")
    await cb(run_id)

    assert len(client.calls) == 1


async def test_non_terminal_is_skipped(repo, queue):
    client = _FakeUplinkClient()
    cb = BcnUplinkCallback(client, repo)

    run_id = _make_terminal(repo, queue, terminal="RUNNING")
    await cb(run_id)

    assert client.calls == []
    assert repo.get_by_run_id(run_id).status == "PENDING"


async def test_run_not_found_is_skipped(repo, queue):
    client = _FakeUplinkClient()
    cb = BcnUplinkCallback(client, repo)

    await cb("nonexistent-run-id")

    assert client.calls == []


async def test_uplink_failure_does_not_raise(repo, queue):
    client = _FakeUplinkClient(raise_exc=True)
    cb = BcnUplinkCallback(client, repo)

    run_id = _make_terminal(repo, queue, terminal="COMPLETED")
    await cb(run_id)  # 不应抛异常

    assert len(client.calls) == 1


async def test_failed_event_state_is_error(repo, queue):
    """FAILED 终态的 ChatEvent.state 应为 'error'。"""
    from secbaas.community.api.bcn import ChatEvent

    captured_events: list = []

    class _CapturingClient(_FakeUplinkClient):
        async def send_event(self, event, bot_id, event_id=None):
            captured_events.append(event)
            return await super().send_event(event, bot_id, event_id)

    client = _CapturingClient()
    cb = BcnUplinkCallback(client, repo)

    run_id = _make_terminal(repo, queue, terminal="FAILED")
    await cb(run_id)

    assert len(captured_events) == 1
    assert isinstance(captured_events[0], ChatEvent)
    assert captured_events[0].state == "error"
    assert captured_events[0].run_id == run_id


async def test_completed_event_state_is_final(repo, queue):
    """COMPLETED 终态的 ChatEvent.state 应为 'final'。"""
    from secbaas.community.api.bcn import ChatEvent

    captured_events: list = []

    class _CapturingClient(_FakeUplinkClient):
        async def send_event(self, event, bot_id, event_id=None):
            captured_events.append(event)
            return await super().send_event(event, bot_id, event_id)

    client = _CapturingClient()
    cb = BcnUplinkCallback(client, repo)

    run_id = _make_terminal(repo, queue, terminal="COMPLETED")
    await cb(run_id)

    assert len(captured_events) == 1
    assert isinstance(captured_events[0], ChatEvent)
    assert captured_events[0].state == "final"
