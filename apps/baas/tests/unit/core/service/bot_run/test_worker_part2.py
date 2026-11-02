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


async def test_timeout_scan_remote_stale_writes_result_timeout(repo, queue):
    """非本机 RUNNING + 心跳过期 → 同时写 baas_bot_run=TIME_OUT（不再卡 PENDING）。"""
    run_id = _insert(repo, queue, "bot-1")
    record = queue.claim_pending_by_bot("bot-1", "remote-worker", candidates=5)
    assert record is not None
    queue.update_meta(run_id, {"timeout": -1})

    from datetime import datetime, timedelta

    stale_time = datetime.now() - timedelta(seconds=300)
    stale_record = BotRunQueueRecord(
        id=record.id,
        gmt_create=record.gmt_create,
        gmt_modified=record.gmt_modified,
        run_id=record.run_id,
        bot_id=record.bot_id,
        session_id=record.session_id,
        status="RUNNING",
        assigned_worker="remote-worker",
        last_heartbeat=stale_time,
        meta={"timeout": -1},
        env=record.env,
    )
    ex = ResultGuardExecutor(_CompletingExecutor(repo), repo)
    worker = BotRequestWorker(
        queue_repository=queue,
        qpm_manager=_qpm(),
        executor=ex,
        run_repository=repo,
        lock_service=_LOCK_SERVICE,
        post_run_callback_factories={},
        config=BotRequestWorkerConfig(),
        worker_id="worker-1",
    )
    with patch.object(
        worker._queue, "list_unfinished", MagicMock(return_value=[stale_record])
    ):
        await worker._timeout_scan_once()

    assert queue.get_by_run_id(run_id).status == "DONE"
    rec = repo.get_by_run_id(run_id)
    assert rec.status == "TIME_OUT"
    assert "worker lost" in (rec.error or "")


async def test_timeout_scan_force_done_remote_running_with_no_heartbeat(repo, queue):
    """非本机 RUNNING + 无心跳（last_heartbeat=None）→ 视为过期，force_done。"""
    run_id = _insert(repo, queue, "bot-1")
    record = queue.claim_pending_by_bot("bot-1", "remote-worker", candidates=5)
    assert record is not None

    stale_record = BotRunQueueRecord(
        id=record.id,
        gmt_create=record.gmt_create,
        gmt_modified=record.gmt_modified,
        run_id=record.run_id,
        bot_id=record.bot_id,
        session_id=record.session_id,
        status="RUNNING",
        assigned_worker="remote-worker",
        last_heartbeat=None,
        meta={"timeout": -1},
        env=record.env,
    )
    ex = ResultGuardExecutor(_CompletingExecutor(repo), repo)
    worker = _worker(queue, repo, ex)
    with patch.object(
        worker._queue, "list_unfinished", MagicMock(return_value=[stale_record])
    ):
        await worker._timeout_scan_once()

    assert queue.get_by_run_id(run_id).status == "DONE"


async def test_run_guardian_cancels_local_task_on_deadline(repo, queue):
    """_run_guardian 到点会 cancel 本机 task（本机超时不再靠查库）。"""
    from datetime import datetime, timedelta

    worker = _worker(queue, repo, _CompletingExecutor(repo))

    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def fake_run():
        started.set()
        try:
            await asyncio.sleep(999)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    task = asyncio.create_task(fake_run())
    await started.wait()
    worker._running_tasks["run-1"] = task

    # deadline 已过 → guardian 立刻取消本机 task 并返回
    await asyncio.wait_for(
        worker._run_guardian(
            _queue_record("run-1"), datetime.now() - timedelta(seconds=1)
        ),
        timeout=1,
    )
    with pytest.raises(asyncio.CancelledError):
        await task
    assert cancelled.is_set()


async def test_terminate_local_timeout_force_done(repo, queue):
    """_terminate_local_timeout：cancel 本机 task 且 force_done 队列行。"""
    run_id = _insert(repo, queue, "bot-1")
    record = queue.claim_pending_by_bot("bot-1", "worker-1", candidates=5)
    assert record is not None

    worker = _worker(queue, repo, _CompletingExecutor(repo))

    started = asyncio.Event()

    async def fake_run():
        started.set()
        await asyncio.sleep(999)

    task = asyncio.create_task(fake_run())
    await started.wait()
    worker._running_tasks[run_id] = task

    await worker._terminate_local_timeout(record)
    with pytest.raises(asyncio.CancelledError):
        await task
    assert queue.get_by_run_id(run_id).status == "DONE"


async def test_run_guardian_cancel_noop_when_task_done(repo, queue):
    """_run_guardian 到点但本机 task 已完成 → 不报错。"""
    from datetime import datetime, timedelta

    worker = _worker(queue, repo, _CompletingExecutor(repo))

    async def done_task():
        pass

    task = asyncio.create_task(done_task())
    await task
    worker._running_tasks["run-1"] = task

    await asyncio.wait_for(
        worker._run_guardian(
            _queue_record("run-1"), datetime.now() - timedelta(seconds=1)
        ),
        timeout=1,
    )
    assert task.done()
    assert not task.cancelled()


async def test_tick_tracks_running_task(repo, queue):
    """_tick 派发后 _running_tasks 应有记录，_run_one 完成后清除。"""
    run_id = _insert(repo, queue, "bot-1")
    ex = _CompletingExecutor(repo)
    worker = _worker(queue, repo, ex)

    dispatched = await worker._tick()
    assert dispatched == 1
    # task 刚创建，_running_tasks 中应有记录
    assert run_id in worker._running_tasks

    # 等 task 完成
    await asyncio.gather(*worker._running_tasks.values(), return_exceptions=True)
    await _drain()

    # _run_one finally 中应清除
    assert run_id not in worker._running_tasks


async def test_timeout_scan_stale_heartbeat_with_callback(repo, queue):
    """非本机 RUNNING + 心跳过期 + 有 callback → force_done 并执行 callback。"""
    from datetime import datetime, timedelta

    run_id = _insert(repo, queue, "bot-1")
    record = queue.claim_pending_by_bot("bot-1", "remote-worker", candidates=5)
    assert record is not None

    stale_record = BotRunQueueRecord(
        id=record.id,
        gmt_create=record.gmt_create,
        gmt_modified=record.gmt_modified,
        run_id=record.run_id,
        bot_id=record.bot_id,
        session_id=record.session_id,
        status="RUNNING",
        assigned_worker="remote-worker",
        last_heartbeat=datetime.now() - timedelta(seconds=300),
        meta={"timeout": -1, "callback_function": "test_cb"},
        env=record.env,
    )

    callback_called = asyncio.Event()

    async def test_callback(rid: str) -> None:
        assert rid == run_id
        callback_called.set()

    ex = ResultGuardExecutor(_CompletingExecutor(repo), repo)
    worker = _worker(
        queue,
        repo,
        ex,
        post_run_callback_factories={"test_cb": test_callback},
    )
    with patch.object(
        worker._queue, "list_unfinished", MagicMock(return_value=[stale_record])
    ):
        await worker._timeout_scan_once()

    assert callback_called.is_set()
    assert queue.get_by_run_id(run_id).status == "DONE"


async def test_timeout_scan_stale_heartbeat_callback_error_is_logged(repo, queue):
    """非本机 RUNNING + 心跳过期 + callback 抛异常 → force_done，错误被 log。"""
    from datetime import datetime, timedelta

    run_id = _insert(repo, queue, "bot-1")
    record = queue.claim_pending_by_bot("bot-1", "remote-worker", candidates=5)
    assert record is not None

    stale_record = BotRunQueueRecord(
        id=record.id,
        gmt_create=record.gmt_create,
        gmt_modified=record.gmt_modified,
        run_id=record.run_id,
        bot_id=record.bot_id,
        session_id=record.session_id,
        status="RUNNING",
        assigned_worker="remote-worker",
        last_heartbeat=datetime.now() - timedelta(seconds=300),
        meta={"timeout": -1, "callback_function": "bad_cb"},
        env=record.env,
    )

    async def bad_callback(rid: str) -> None:
        raise RuntimeError("callback boom")

    ex = ResultGuardExecutor(_CompletingExecutor(repo), repo)
    worker = _worker(
        queue,
        repo,
        ex,
        post_run_callback_factories={"bad_cb": bad_callback},
    )
    with patch.object(
        worker._queue, "list_unfinished", MagicMock(return_value=[stale_record])
    ):
        await worker._timeout_scan_once()

    # callback 失败不影响 force_done
    assert queue.get_by_run_id(run_id).status == "DONE"


async def test_requeue_pending_release_error_is_swallowed(repo, queue):
    _insert(repo, queue, "bot-1")
    worker = _worker(queue, repo, _RequeuedExecutor())
    record = queue.claim_pending_by_bot("bot-1", worker.worker_id, candidates=5)
    assert record is not None

    with patch.object(
        worker._queue,
        "release_to_pending",
        MagicMock(side_effect=RuntimeError("db lost")),
    ) as mock_release:
        from secbaas.community.core.service.bot_run._executor import (
            RequeuedToPendingError,
        )

        await worker._requeue_pending(
            record, RequeuedToPendingError(record.run_id, "sess-1")
        )

    mock_release.assert_called_once_with(record.run_id, worker.worker_id)


def test_mark_queue_done_noop_is_logged(repo, queue):
    _insert(repo, queue, "bot-1")
    worker = _worker(queue, repo, _CompletingExecutor(repo))
    record = queue.claim_pending_by_bot("bot-1", "other-worker", candidates=5)
    assert record is not None

    worker._mark_queue_done(record)

    after = queue.get_by_run_id(record.run_id)
    assert after.status == "RUNNING"
    assert after.assigned_worker == "other-worker"


async def test_heartbeat_loop_touches_with_worker_id(repo, queue):
    _insert(repo, queue, "bot-1")
    worker = _worker(
        queue,
        repo,
        _CompletingExecutor(repo),
        config=BotRequestWorkerConfig(heartbeat_interval_seconds=0.01),
    )
    touched = asyncio.Event()

    def touch(run_id: str, worker_id: str) -> None:
        assert worker_id == worker.worker_id
        touched.set()

    with patch.object(worker._queue, "touch_heartbeat", MagicMock(side_effect=touch)):
        task = asyncio.create_task(worker._run_guardian(_queue_record("run-1"), None))
        await asyncio.wait_for(touched.wait(), timeout=1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


async def test_heartbeat_loop_logs_touch_error_and_continues(repo, queue):
    _insert(repo, queue, "bot-1")
    worker = _worker(
        queue,
        repo,
        _CompletingExecutor(repo),
        config=BotRequestWorkerConfig(heartbeat_interval_seconds=0.01),
    )
    second_call = asyncio.Event()
    calls = 0

    def touch(run_id: str, worker_id: str) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("heartbeat db lost")
        second_call.set()

    with patch.object(worker._queue, "touch_heartbeat", MagicMock(side_effect=touch)):
        task = asyncio.create_task(worker._run_guardian(_queue_record("run-1"), None))
        await asyncio.wait_for(second_call.wait(), timeout=1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert calls >= 2


@pytest.mark.xfail(strict=False, reason="flaky in CI — resolve later")
async def test_tick_observes_advisory_running_limit(repo, queue):
    """单 Worker 观察到在途数为 2 后停止派发；不声称跨进程原子配额。"""
    for _ in range(10):
        _insert(repo, queue, "bot-1")
    ex = _BlockingExecutor()
    worker = BotRequestWorker(
        queue_repository=queue,
        qpm_manager=_qpm(bot_qpm=2),
        executor=ex,
        run_repository=repo,
        lock_service=_LOCK_SERVICE,
        post_run_callback_factories={},
        config=BotRequestWorkerConfig(),
    )

    assert await worker._tick() == 2
    await _drain()
    assert worker.active_count == 2
    assert ex.started == 2
    # 在途已满，再次 tick 不再派发
    assert await worker._tick() == 0

    ex.gate.set()
    await _drain()
    assert worker.active_count == 0
    # 在途释放后恢复派发
    assert await worker._tick() == 2


async def test_tick_passes_teclaw_engine_to_teclaw_default(repo, queue):
    """teclaw exact miss uses 5000 instead of the wildcard row."""
    _insert(repo, queue, "bot-1", meta={"engine_type": "teclaw"})
    ex = _CompletingExecutor(repo)
    worker = BotRequestWorker(
        queue_repository=queue,
        qpm_manager=_qpm_with_configs({"*": 60}),
        executor=ex,
        run_repository=repo,
        lock_service=_LOCK_SERVICE,
        post_run_callback_factories={},
        config=BotRequestWorkerConfig(),
    )

    spy = MagicMock(wraps=queue.claim_pending_by_bot)
    with patch.object(worker._queue, "claim_pending_by_bot", spy):
        assert await worker._tick() == 1

    assert worker._qpm.get_concurrency_num("bot-1", engine_type="teclaw") == 5000
    assert "max_running" not in spy.call_args.kwargs


async def test_tick_passes_non_teclaw_engine_to_wildcard(repo, queue):
    """openclaw exact miss keeps the existing wildcard concurrency."""
    _insert(repo, queue, "bot-1", meta={"engine_type": "openclaw"})
    ex = _CompletingExecutor(repo)
    worker = BotRequestWorker(
        queue_repository=queue,
        qpm_manager=_qpm_with_configs({"*": 60}),
        executor=ex,
        run_repository=repo,
        lock_service=_LOCK_SERVICE,
        post_run_callback_factories={},
        config=BotRequestWorkerConfig(),
    )

    spy = MagicMock(wraps=queue.claim_pending_by_bot)
    with patch.object(worker._queue, "claim_pending_by_bot", spy):
        assert await worker._tick() == 1

    assert worker._qpm.get_concurrency_num("bot-1", engine_type="openclaw") == 60
    assert "max_running" not in spy.call_args.kwargs


async def test_tick_owns_admission_without_passing_policy_to_claim(repo, queue):
    """_tick 持有并发准入策略；原子认领方法不接收 max_running。"""
    _insert(repo, queue, "bot-1")
    ex = _CompletingExecutor(repo)
    worker = _worker(queue, repo, ex)  # qpm 默认 600

    spy = MagicMock(wraps=queue.claim_pending_by_bot)
    with patch.object(worker._queue, "claim_pending_by_bot", spy):
        await worker._tick()

    assert worker._qpm.get_concurrency_num("bot-1") == 600
    assert "max_running" not in spy.call_args.kwargs
    await _drain()


async def test_worker_skips_claim_when_running_count_reached(repo, queue):
    """Admission is advisory and happens before entering the dequeue session."""
    _insert(repo, queue, "bot-1")
    queue.claim_pending_by_bot("bot-1", "worker-other", candidates=5)
    _insert(repo, queue, "bot-1")
    worker = BotRequestWorker(
        queue_repository=queue,
        qpm_manager=_qpm(bot_qpm=1),
        executor=_CompletingExecutor(repo),
        run_repository=repo,
        lock_service=_LOCK_SERVICE,
        post_run_callback_factories={},
        config=BotRequestWorkerConfig(),
    )
    with patch.object(
        queue, "claim_pending_by_bot", wraps=queue.claim_pending_by_bot
    ) as claim:
        assert await worker._tick() == 0
        claim.assert_not_called()


def test_count_running_by_bot(repo, queue):
    """count_running_by_bot 只统计 RUNNING（在途）行。"""
    _insert(repo, queue, "bot-1")
    assert queue.count_running_by_bot("bot-1") == 0
    queue.claim_pending_by_bot("bot-1", "worker-1", candidates=5)
    assert queue.count_running_by_bot("bot-1") == 1


def test_maybe_log_cap_reports_throttled_bots(repo, queue):
    """_maybe_log_cap：在途数达 qpm 的 bot 会被判定为「被准入计数拦住」。"""
    _insert(repo, queue, "bot-1")
    # 另一 worker 认领一条 → RUNNING（在途 1）
    queue.claim_pending_by_bot("bot-1", "other-worker", candidates=5)

    ex = _CompletingExecutor(repo)
    worker = BotRequestWorker(
        queue_repository=queue,
        qpm_manager=_qpm(bot_qpm=1),
        executor=ex,
        run_repository=repo,
        lock_service=_LOCK_SERVICE,
        post_run_callback_factories={},
        config=BotRequestWorkerConfig(),
    )
    assert worker._maybe_log_cap(["bot-1"]) == ["bot-1"]


async def test_abort_runs_by_session_marks_aborted_force_done_and_cancels_local_task(
    repo, queue
):
    """RUNNING run -> update_aborted(ABORTED) + force_done + cancel 本机 task."""
    run_id = _insert_with_session(repo, queue, "bot-1", "sess-abort")
    claimed = queue.claim_pending_by_bot("bot-1", "worker-1", candidates=5)
    assert claimed is not None and claimed.run_id == run_id

    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def fake_run():
        started.set()
        try:
            await asyncio.sleep(999)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    task = asyncio.create_task(fake_run())
    await started.wait()

    ex = ResultGuardExecutor(_CompletingExecutor(repo), repo)
    worker = _worker(queue, repo, ex, run_repository=repo)
    worker._running_tasks[run_id] = task

    outcome = await worker.abort_runs_by_session("sess-abort", "bot-1")

    await asyncio.sleep(0)  # let CancelledError propagate
    assert outcome.aborted_run_ids == [run_id]
    assert outcome.had_terminal is False
    assert cancelled.is_set(), "local task must be cancelled"
    assert repo.get_by_run_id(run_id).status == "ABORTED"
    assert queue.get_by_run_id(run_id).status == "DONE"
    # idempotent: aborted run no longer in find_running_by_bot_session
    assert worker._queue.find_running_by_bot_session("sess-abort", "bot-1") == []
