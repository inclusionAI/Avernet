"""BotRequestWorker 单元测试（阶段一，双表）。

用真实内存 SQLite 仓库 + 假 executor，验证发现→限流→认领→派发→并发控制→
心跳兜底等队列循环语义。队列工作项在 baas_bot_run_queue，结果在 baas_bot_run。
asyncio_mode=auto，异步用例直接 async def。
"""

from __future__ import annotations

import asyncio
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from secbaas.community.core.database import DatabaseManager
from secbaas.community.core.repository.bot_run import OrmBotRunRepository
from secbaas.community.core.repository.bot_run_queue import (
    BotRunQueueRecord,
    OrmBotRunQueueRepository,
)
from secbaas.community.core.service.bot_run import BotBindingResolver, BotRunner
from secbaas.community.core.service.bot_run._bot_concurrency import (
    BotConcurrencyManager,
)
from secbaas.community.core.service.bot_run._executor import ResultGuardExecutor
from secbaas.community.core.service.bot_run._worker import (
    BotRequestWorker,
    BotRequestWorkerConfig,
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
    orig_plugin = mgr._plugin
    mgr.init_plugin(plugin)
    try:
        yield mgr
    finally:
        mgr._plugin = orig_plugin
        plugin._sync_engine.dispose()


@pytest.fixture
def repo(db: DatabaseManager) -> OrmBotRunRepository:
    return OrmBotRunRepository(database=db)


@pytest.fixture
def queue(db: DatabaseManager) -> OrmBotRunQueueRepository:
    return OrmBotRunQueueRepository(database=db)


class _ConfigQpmRepo:
    def __init__(self, configs: dict[str, int]):
        self._configs = configs

    def list_all(self):
        from secbaas.community.core.repository.bot_qpm import BotQpmRecord

        return [
            BotQpmRecord(
                id=i,
                bot_id=bot_id,
                qpm=qpm,
                env=None,
                gmt_create=None,
                gmt_modified=None,
            )
            for i, (bot_id, qpm) in enumerate(self._configs.items())
        ]


class _QpmRepo:
    def __init__(self, bot_qpm: int = 600):
        self._bot_qpm = bot_qpm

    def list_all(self):
        from secbaas.community.core.repository.bot_qpm import BotQpmRecord

        return [
            BotQpmRecord(
                id=1,
                bot_id="bot-1",
                qpm=self._bot_qpm,
                env=None,
                gmt_create=None,
                gmt_modified=None,
            )
        ]


def _qpm(bot_qpm: int = 600) -> BotConcurrencyManager:
    mgr = BotConcurrencyManager(
        _QpmRepo(bot_qpm=bot_qpm),
        refresh_interval_seconds=999,
    )
    # 预设 _configs，避免依赖 refresh() 的 import 时序
    mgr._configs = {"bot-1": bot_qpm}
    return mgr


def _qpm_with_configs(configs: dict[str, int]) -> BotConcurrencyManager:
    manager = BotConcurrencyManager(
        _ConfigQpmRepo(configs), refresh_interval_seconds=999
    )
    manager.refresh()
    return manager


class _CompletingExecutor:
    """正常完成：把结果写成 COMPLETED（baas_bot_run），记录执行过的 run_id。"""

    def __init__(self, repo: OrmBotRunRepository):
        self._repo = repo
        self.executed: list[str] = []

    async def execute(self, record: BotRunQueueRecord) -> None:
        self.executed.append(record.run_id)
        self._repo.update_result(record.run_id, "done", {"session_id": "s"})


class _BlockingExecutor:
    """阻塞在事件上，用于测试并发上限。"""

    def __init__(self):
        self.gate = asyncio.Event()
        self.started = 0

    async def execute(self, record: BotRunQueueRecord) -> None:
        self.started += 1
        await self.gate.wait()


class _RaisingExecutor:
    """抛异常，用于测试兜底标记 FAILED。"""

    async def execute(self, record: BotRunQueueRecord) -> None:
        raise RuntimeError("boom")


class _RequeuedExecutor:
    """抛 RequeuedToPendingError，用于测试 requeue 路径。"""

    def __init__(self, session_id: str = "sess-1"):
        self._session_id = session_id

    async def execute(self, record: BotRunQueueRecord) -> None:
        from secbaas.community.core.service.bot_run._executor import (
            RequeuedToPendingError,
        )

        raise RequeuedToPendingError(record.run_id, self._session_id)


class _CancellableBlockingExecutor:
    """阻塞在事件上，执行/取消时分别设置事件。"""

    def __init__(self):
        self.gate = asyncio.Event()
        self.started = asyncio.Event()
        self.cancelled = asyncio.Event()

    async def execute(self, record: BotRunQueueRecord) -> None:
        self.started.set()
        try:
            await self.gate.wait()
        except asyncio.CancelledError:
            self.cancelled.set()
            raise


class _CancellableBlockingExecutor:
    """阻塞在事件上，被取消时设置事件。"""

    def __init__(self):
        self.gate = asyncio.Event()
        self.started = 0
        self.cancelled = asyncio.Event()

    async def execute(self, record: BotRunQueueRecord) -> None:
        self.started += 1
        try:
            await self.gate.wait()
        except asyncio.CancelledError:
            self.cancelled.set()
            raise


def _insert(
    repo: OrmBotRunRepository,
    queue: OrmBotRunQueueRepository,
    bot_id: str,
    *,
    meta: dict | None = None,
) -> str:
    """双写：结果行（baas_bot_run）+ 队列工作项（baas_bot_run_queue）。"""
    run_id = uuid4().hex
    repo.insert_run(
        run_id=run_id,
        bot_id=bot_id,
        api_key_prefix="sk-",
        message_long="m",
        metadata=None,
    )
    queue.insert_queue(run_id=run_id, bot_id=bot_id, meta=meta)
    return run_id


def _queue_record(run_id: str, *, meta: dict | None = None) -> BotRunQueueRecord:
    """构造一个最小 BotRunQueueRecord（供 _run_guardian 直接调用测试）。"""
    return BotRunQueueRecord(
        id=1,
        gmt_create=None,
        gmt_modified=None,
        run_id=run_id,
        bot_id="bot-1",
        session_id=None,
        status="RUNNING",
        assigned_worker=None,
        last_heartbeat=None,
        meta=meta or {},
    )


class _FakeLockCtx:
    acquired = True

    def __enter__(self) -> _FakeLockCtx:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False


class _FakeLockService:
    """测试用假锁服务：try_lock 恒获取成功。"""

    def try_lock(self, *, lock_name, expire_seconds, block=False):
        return _FakeLockCtx()


_LOCK_SERVICE = _FakeLockService()


def _worker(queue, repo, ex, **kw) -> BotRequestWorker:
    return BotRequestWorker(
        queue_repository=queue,
        qpm_manager=_qpm(),
        executor=ex,
        run_repository=kw.pop("run_repository", repo),
        lock_service=kw.pop("lock_service", _LOCK_SERVICE),
        post_run_callback_factories=kw.pop("post_run_callback_factories", {}),
        config=kw.pop("config", BotRequestWorkerConfig()),
        worker_id=kw.pop("worker_id", "worker-1"),
        **kw,
    )


async def _drain(times: int = 5):
    for _ in range(times):
        await asyncio.sleep(0)


from unittest.mock import patch  # noqa: E402

from secbaas.community.core.service.bot_run._worker import (  # noqa: E402
    _trace_context_from_meta,
)


def _insert_with_session(
    repo: OrmBotRunRepository,
    queue: OrmBotRunQueueRepository,
    bot_id: str,
    session_id: str,
) -> str:
    """双写并显式设置 queue.session_id（chat.abort 按 session 查询需要）。"""
    run_id = uuid4().hex
    repo.insert_run(
        run_id=run_id,
        bot_id=bot_id,
        api_key_prefix="sk-",
        message_long="m",
        metadata=None,
    )
    queue.insert_queue(run_id=run_id, bot_id=bot_id, session_id=session_id)
    return run_id


def _make_bot_runner_for_abort(
    repo: OrmBotRunRepository,
    *,
    binding_return: Any = None,
    service_abort_side_effect: Exception | None = None,
):
    """构造用于测试 ``BotRunner.abort`` 的 Runner。"""
    from secbaas.community.api.device_manage import ErrorCode, PaasError
    from secbaas.community.core.service.bot_run._noop_message_dispatcher import (
        NoopMessageDispatcher,
    )
    from secbaas.community.plugins.eval_env.stub import NoopEvalSessionLog
    from secbaas.community.spi.bot_service import BotBindingData

    class _FakeBotService:
        def __init__(self):
            self.calls: list[dict[str, Any]] = []

        async def abort(
            self,
            *,
            session_id: str,
            binding_info: Any,
            context: Any = None,
        ) -> None:
            self.calls.append(
                {
                    "session_id": session_id,
                    "binding_info": binding_info,
                    "context": context,
                }
            )
            if service_abort_side_effect is not None:
                raise service_abort_side_effect

    fake_service = _FakeBotService()

    class _FakeSelector:
        def select(self, binding_info: Any) -> Any:
            return fake_service

    class _FakePlugin:
        async def get_binding(
            self,
            bot_id: str,
            owner_id: str,
            stage: str,
            *,
            default_tag: str | None = None,
        ) -> Any:
            if isinstance(binding_return, Exception):
                raise binding_return
            return binding_return

        async def report(self, payload: Any) -> None:
            pass

    if binding_return is None:
        binding_return = BotBindingData(
            bot_id="bot-1",
            owner_id="entity-1",
            bot_type="personal",
            engine_type="openclaw",
            binding_id=1,
            device_provider="baas",
            device_id="device-1",
        )

    runner = BotRunner(
        bot_service_selector=_FakeSelector(),
        run_repository=repo,
        bot_service_plugin=_FakePlugin(),
        binding_resolver=BotBindingResolver(_FakePlugin()),
        dispatchers=[NoopMessageDispatcher()],
        system_config_service=MagicMock(),  # type: ignore[arg-type]
        eval_session_log=NoopEvalSessionLog(),
    )
    return runner, fake_service


class _NotAcquiredLockCtx:
    acquired = False

    def __enter__(self) -> _NotAcquiredLockCtx:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False


class _NotAcquiredLockService:
    """测试用假锁服务：try_lock 恒未获取。"""

    def try_lock(self, *, lock_name, expire_seconds, block=False):
        return _NotAcquiredLockCtx()


async def test_abort_poll_loop_continues_when_queue_record_missing(repo, queue):
    """get_by_run_id 返回 None 时，poll loop 继续下一轮。"""
    run_id = _insert_with_session(repo, queue, "bot-1", "sess-poll-missing")
    record = queue.get_by_run_id(run_id)
    worker = _worker(
        queue,
        repo,
        _CompletingExecutor(repo),
        worker_id="worker-poll-missing",
        config=BotRequestWorkerConfig(abort_poll_interval_seconds=0.05),
    )
    worker._queue.get_by_run_id = MagicMock(return_value=None)

    async def short_run() -> None:
        await asyncio.sleep(0.15)

    run_task = asyncio.create_task(short_run())
    poll_task = asyncio.create_task(worker._abort_poll_loop(record, run_task))

    await asyncio.wait_for(poll_task, timeout=2)


async def test_abort_poll_loop_swallows_engine_notifier_error(repo, queue):
    """engine abort notifier 抛异常时，poll loop 记录日志仍继续取消 task。"""
    run_id = _insert_with_session(repo, queue, "bot-1", "sess-notifier-fail")
    record = queue.get_by_run_id(run_id)

    async def failing_notifier(session_id: str, rid: str | None) -> None:
        raise RuntimeError("notifier boom")

    worker = _worker(
        queue,
        repo,
        _CompletingExecutor(repo),
        worker_id="worker-notifier-fail",
        config=BotRequestWorkerConfig(abort_poll_interval_seconds=0.05),
        engine_abort_notifier=failing_notifier,
    )

    run_cancelled = asyncio.Event()

    async def fake_run() -> None:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            run_cancelled.set()
            raise

    run_task = asyncio.create_task(fake_run())
    poll_task = asyncio.create_task(worker._abort_poll_loop(record, run_task))

    await asyncio.sleep(0.1)
    queue.update_meta(run_id, {"abort_requested": True})

    await asyncio.wait_for(poll_task, timeout=2)
    await asyncio.wait_for(run_cancelled.wait(), timeout=2)

    assert queue.get_by_run_id(run_id).status == "DONE"


async def test_abort_poll_loop_logs_when_queue_lookup_fails(repo, queue):
    """get_by_run_id 抛异常时，poll loop 捕获并记录日志后继续。"""
    run_id = _insert_with_session(repo, queue, "bot-1", "sess-poll-exception")
    record = queue.get_by_run_id(run_id)
    queue.update_meta(run_id, {"abort_requested": True})

    worker = _worker(
        queue,
        repo,
        _CompletingExecutor(repo),
        worker_id="worker-poll-exception",
        config=BotRequestWorkerConfig(abort_poll_interval_seconds=0.05),
    )
    original_get_by_run_id = worker._queue.get_by_run_id
    worker._queue.get_by_run_id = MagicMock(
        side_effect=[RuntimeError("db boom"), record]
    )

    run_cancelled = asyncio.Event()

    async def fake_run() -> None:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            run_cancelled.set()
            raise

    run_task = asyncio.create_task(fake_run())
    poll_task = asyncio.create_task(worker._abort_poll_loop(record, run_task))

    await asyncio.wait_for(poll_task, timeout=2)
    await asyncio.wait_for(run_cancelled.wait(), timeout=2)

    worker._queue.get_by_run_id = original_get_by_run_id
    assert queue.get_by_run_id(run_id).status == "DONE"


async def test_timeout_scan_skips_when_lock_not_acquired(repo, queue):
    """全局超时扫描未抢到单飞锁时直接跳过，不做任何对账。"""
    worker = _worker(
        queue, repo, _CompletingExecutor(repo), lock_service=_NotAcquiredLockService()
    )
    scan = MagicMock()
    queue.list_unfinished = scan
    await worker._timeout_scan_once()
    scan.assert_not_called()


async def test_abort_continues_when_update_error_fails(repo, queue):
    """update_error 抛异常时 abort 仍继续 request_abort + force_done。"""
    run_id = _insert_with_session(repo, queue, "bot-1", "sess-abort-dberr")
    queue.claim_pending_by_bot("bot-1", "wA", candidates=5)
    repo.update_error = MagicMock(side_effect=RuntimeError("db down"))
    ex = ResultGuardExecutor(_CompletingExecutor(repo), repo)
    worker = _worker(queue, repo, ex, run_repository=repo)

    outcome = await worker.abort_runs_by_session("sess-abort-dberr", "bot-1")

    assert outcome.aborted_run_ids == [run_id]
    assert queue.get_by_run_id(run_id).status == "DONE"
    assert queue.is_abort_requested(run_id) is True


async def test_tick_breaks_at_max_concurrent(repo, queue):
    """本机并发额度占满后，本轮不再认领后续 bot 的 PENDING。"""
    run_a = _insert(repo, queue, "bot-1")
    run_b = _insert(repo, queue, "bot-2")
    ex = _BlockingExecutor()
    worker = _worker(queue, repo, ex, config=BotRequestWorkerConfig(max_concurrent=1))
    mgr = _qpm()
    mgr._configs = {"bot-1": 600, "bot-2": 600}
    worker._qpm = mgr
    try:
        dispatched = await worker._tick()
        assert dispatched == 1
        await asyncio.sleep(0)  # 让已派发 task 进入 execute（挂起在 gate）
        assert ex.started == 1
        statuses = {
            queue.get_by_run_id(run_a).status,
            queue.get_by_run_id(run_b).status,
        }
        assert statuses == {"RUNNING", "PENDING"}
    finally:
        ex.gate.set()
        await _drain(20)


async def test_tick_skips_bot_without_qpm_config(repo, queue):
    """无并发上限配置的 bot 本轮跳过（不认领），有配置的照常派发。"""
    run_1 = _insert(repo, queue, "bot-1")
    run_x = _insert(repo, queue, "bot-x")
    ex = _CompletingExecutor(repo)
    worker = _worker(queue, repo, ex, config=BotRequestWorkerConfig(max_concurrent=5))

    dispatched = await worker._tick()
    await _drain(10)

    assert dispatched == 1
    assert ex.executed == [run_1]
    assert queue.get_by_run_id(run_x).status == "PENDING"


def test_maybe_log_cap_throttled_within_interval(repo, queue):
    """节流窗口内的第二次调用直接返回空。"""
    worker = _worker(queue, repo, _CompletingExecutor(repo))
    worker._last_cap_warn = time.monotonic()
    assert worker._maybe_log_cap(["bot-1"]) == []


def test_maybe_log_cap_reports_capped_bot(repo, queue):
    """RUNNING 在途数达到该 bot 并发上限时被判定为 capped。"""
    _insert(repo, queue, "bot-1")
    queue.claim_pending_by_bot("bot-1", "wA", candidates=5)
    worker = _worker(queue, repo, _CompletingExecutor(repo))
    worker._qpm = _qpm(bot_qpm=1)
    assert worker._maybe_log_cap(["bot-1"]) == ["bot-1"]


def test_maybe_log_cap_skips_bot_without_qpm(repo, queue):
    """无 qpm 配置的 bot 不做 capped 判定。"""
    worker = _worker(queue, repo, _CompletingExecutor(repo))
    assert worker._maybe_log_cap(["bot-x"]) == []


def test_maybe_log_cap_swallows_count_error(repo, queue):
    """count_running_by_bot 抛异常仅记录日志，不向上抛。"""
    worker = _worker(queue, repo, _CompletingExecutor(repo))
    queue.count_running_by_bot = MagicMock(side_effect=RuntimeError("db boom"))
    assert worker._maybe_log_cap(["bot-1"]) == []


async def test_post_run_awaits_callback(repo, queue):
    """有回调时 post_run await 执行并传入 run_id。"""
    worker = _worker(queue, repo, _CompletingExecutor(repo))
    record = queue.get_by_run_id(_insert(repo, queue, "bot-1"))
    cb = AsyncMock()
    await worker._post_run(record, cb)
    cb.assert_awaited_once_with(record.run_id)


async def test_post_run_swallows_callback_error(repo, queue):
    """回调抛异常仅记录日志，不影响主流程。"""
    worker = _worker(queue, repo, _CompletingExecutor(repo))
    record = queue.get_by_run_id(_insert(repo, queue, "bot-1"))
    cb = AsyncMock(side_effect=RuntimeError("cb boom"))
    await worker._post_run(record, cb)
    cb.assert_awaited_once_with(record.run_id)
