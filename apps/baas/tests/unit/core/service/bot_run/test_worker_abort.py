"""BotRequestWorker 单元测试（阶段一，双表）。

用真实内存 SQLite 仓库 + 假 executor，验证发现→限流→认领→派发→并发控制→
心跳兜底等队列循环语义。队列工作项在 baas_bot_run_queue，结果在 baas_bot_run。
asyncio_mode=auto，异步用例直接 async def。
"""

from __future__ import annotations

import asyncio
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
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

from .test_worker import (
    _LOCK_SERVICE,
    _BlockingExecutor,
    _CancellableBlockingExecutor,
    _CompletingExecutor,
    _drain,
    _FakeLockCtx,
    _insert,
    _qpm,
    _queue_record,
    _RequeuedExecutor,
    _worker,
)
from .test_worker import (
    _mock_db_manager as _mock_db_manager,
)
from .test_worker import (
    db as db,
)
from .test_worker import (
    queue as queue,
)
from .test_worker import (
    repo as repo,
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


async def test_abort_runs_by_session_pending_record_left_untouched(repo, queue):
    """PENDING record (not yet claimed) is NOT aborted; only RUNNING is cancelled."""
    run_id = _insert_with_session(repo, queue, "bot-1", "sess-abort")

    ex = ResultGuardExecutor(_CompletingExecutor(repo), repo)
    worker = _worker(queue, repo, ex, run_repository=repo)

    outcome = await worker.abort_runs_by_session("sess-abort", "bot-1")

    assert outcome.aborted_run_ids == []
    assert outcome.had_terminal is False
    # PENDING 不动，由超时扫描兜底
    assert queue.get_by_run_id(run_id).status == "PENDING"
    assert repo.get_by_run_id(run_id).status != "ABORTED"


async def test_abort_runs_by_session_no_run_record_returns_aborted_false(repo, queue):
    """Session with no run record -> aborted:false, had_terminal:false."""
    ex = ResultGuardExecutor(_CompletingExecutor(repo), repo)
    worker = _worker(queue, repo, ex, run_repository=repo)

    outcome = await worker.abort_runs_by_session("no-such-session", "bot-1")

    assert outcome.aborted_run_ids == []
    assert outcome.had_terminal is False


async def test_abort_runs_by_session_terminal_record_had_terminal_true(repo, queue):
    """No abortable run but DONE record exists -> aborted_run_ids empty, had_terminal True."""
    run_id = _insert_with_session(repo, queue, "bot-1", "sess-abort")
    claimed = queue.claim_pending_by_bot("bot-1", "worker-1", candidates=5)
    assert claimed is not None and claimed.run_id == run_id
    assert queue.mark_done(run_id, "worker-1") == 1

    ex = ResultGuardExecutor(_CompletingExecutor(repo), repo)
    worker = _worker(queue, repo, ex, run_repository=repo)

    outcome = await worker.abort_runs_by_session("sess-abort", "bot-1")

    assert outcome.aborted_run_ids == []
    assert outcome.had_terminal is True


async def test_abort_runs_by_session_empty_session_or_bot_returns_empty(repo, queue):
    """Empty session_id or bot_id short-circuits to empty outcome."""
    ex = ResultGuardExecutor(_CompletingExecutor(repo), repo)
    worker = _worker(queue, repo, ex, run_repository=repo)

    assert (await worker.abort_runs_by_session("", "bot-1")).aborted_run_ids == []
    assert (await worker.abort_runs_by_session("sess", "")).aborted_run_ids == []
    out = await worker.abort_runs_by_session("", "")
    assert out.aborted_run_ids == [] and out.had_terminal is False


async def test_abort_runs_by_session_engine_notifier_best_effort(repo, queue):
    """engine_abort_notifier is awaited once per (bot,session); failure logged, not raised."""
    run_id = _insert_with_session(repo, queue, "bot-1", "sess-abort")
    claimed = queue.claim_pending_by_bot("bot-1", "worker-1", candidates=5)
    assert claimed is not None and claimed.run_id == run_id

    notified = asyncio.Event()

    async def notifier(session_id: str, rid: str | None) -> None:
        assert session_id == "sess-abort"
        assert rid == run_id
        notified.set()

    ex = ResultGuardExecutor(_CompletingExecutor(repo), repo)
    worker = _worker(
        queue, repo, ex, run_repository=repo, engine_abort_notifier=notifier
    )

    outcome = await worker.abort_runs_by_session("sess-abort", "bot-1")

    assert outcome.aborted_run_ids == [run_id]
    assert notified.is_set()


async def test_abort_runs_by_session_engine_notifier_error_swallowed(repo, queue):
    """engine_abort_notifier raising must not fail the abort."""
    run_id = _insert_with_session(repo, queue, "bot-1", "sess-abort")
    claimed = queue.claim_pending_by_bot("bot-1", "worker-1", candidates=5)
    assert claimed is not None and claimed.run_id == run_id

    async def notifier(session_id: str, rid: str | None) -> None:
        raise RuntimeError("engine notify boom")

    ex = ResultGuardExecutor(_CompletingExecutor(repo), repo)
    worker = _worker(
        queue, repo, ex, run_repository=repo, engine_abort_notifier=notifier
    )

    outcome = await worker.abort_runs_by_session("sess-abort", "bot-1")

    assert outcome.aborted_run_ids == [run_id]
    assert repo.get_by_run_id(run_id).status == "ABORTED"
    assert queue.get_by_run_id(run_id).status == "DONE"


async def test_abort_runs_by_session_without_run_repo_skips_update_aborted(repo, queue):
    """When run_repository is None, abort still force_done + cancel (best-effort).

    No update_aborted call is made because there is no run_repository.
    """
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
    # run_repository NOT passed -> defaults None
    worker = _worker(queue, repo, ex)
    worker._running_tasks[run_id] = task

    outcome = await worker.abort_runs_by_session("sess-abort", "bot-1")

    await asyncio.sleep(0)
    assert outcome.aborted_run_ids == [run_id]
    assert cancelled.is_set()
    assert queue.get_by_run_id(run_id).status == "DONE"


async def test_abort_runs_by_session_remote_worker_marks_abort_meta(repo, queue):
    """非本机 RUNNING run 被 abort 时，应写 abort_requested 到 meta 并 force_done。"""
    run_id = _insert_with_session(repo, queue, "bot-1", "sess-abort")
    claimed = queue.claim_pending_by_bot("bot-1", "worker-a", candidates=5)
    assert claimed is not None and claimed.run_id == run_id

    ex = ResultGuardExecutor(_CompletingExecutor(repo), repo)
    # worker-b 没有本地 task，模拟多机场景
    worker_b = _worker(queue, repo, ex, worker_id="worker-b", run_repository=repo)

    outcome = await worker_b.abort_runs_by_session("sess-abort", "bot-1")

    assert outcome.aborted_run_ids == [run_id]
    assert outcome.had_terminal is False
    assert queue.get_by_run_id(run_id).status == "DONE"
    assert repo.get_by_run_id(run_id).status == "ABORTED"
    assert queue.is_abort_requested(run_id) is True


async def test_abort_poll_loop_cancels_run_task_and_force_done(repo, queue):
    """_abort_poll_loop 感知到 abort 信号后取消 task 并 force_done。"""
    run_id = _insert_with_session(repo, queue, "bot-1", "sess-abort")
    record = queue.get_by_run_id(run_id)

    worker = _worker(
        queue,
        repo,
        _CompletingExecutor(repo),
        worker_id="worker-1",
        config=BotRequestWorkerConfig(abort_poll_interval_seconds=0.05),
    )

    run_cancelled = asyncio.Event()

    async def _fake_run():
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            run_cancelled.set()
            raise

    run_task = asyncio.create_task(_fake_run())
    poll_task = asyncio.create_task(worker._abort_poll_loop(record, run_task))

    # 给轮询一点时间进入等待
    await asyncio.sleep(0.1)
    queue.update_meta(run_id, {"abort_requested": True})

    # 轮询任务应在感知到信号后退出
    await asyncio.wait_for(poll_task, timeout=2)
    # run_task 应被取消
    await asyncio.wait_for(run_cancelled.wait(), timeout=2)

    assert queue.get_by_run_id(run_id).status == "DONE"


async def test_abort_runs_by_session_group_chat_other_bot_running_not_killed(
    repo, queue
):
    """群聊：abort bot-A，同 session 下 bot-B 的 RUNNING run 不被取消。"""
    run_a = _insert_with_session(repo, queue, "bot-A", "sess-group")
    run_b = _insert_with_session(repo, queue, "bot-B", "sess-group")
    # 各自 claim -> RUNNING
    claimed_a = queue.claim_pending_by_bot("bot-A", "wA", candidates=5)
    claimed_b = queue.claim_pending_by_bot("bot-B", "wB", candidates=5)
    assert claimed_a is not None and claimed_a.run_id == run_a
    assert claimed_b is not None and claimed_b.run_id == run_b

    ex = ResultGuardExecutor(_CompletingExecutor(repo), repo)
    worker = _worker(queue, repo, ex, run_repository=repo)

    outcome = await worker.abort_runs_by_session("sess-group", "bot-A")

    assert outcome.aborted_run_ids == [run_a]
    # bot-B 的 RUNNING run 不受影响
    assert queue.get_by_run_id(run_b).status == "RUNNING"
    assert repo.get_by_run_id(run_b).status not in ("FAILED", "ABORTED")


async def test_abort_runs_by_session_group_chat_target_bot_no_running_410_dimension(
    repo, queue
):
    """群聊：abort bot-A，bot-A 无 RUNNING 但 bot-B 有 -> 不影响任何 run，按 bot-A 维度判 410/200。"""
    _run_a = _insert_with_session(repo, queue, "bot-A", "sess-group")
    run_b = _insert_with_session(repo, queue, "bot-B", "sess-group")
    # bot-B claim 成 RUNNING；bot-A 仍是 PENDING（不会命中 find_running_by_bot_session）
    claimed_b = queue.claim_pending_by_bot("bot-B", "wB", candidates=5)
    assert claimed_b is not None and claimed_b.run_id == run_b

    ex = ResultGuardExecutor(_CompletingExecutor(repo), repo)
    worker = _worker(queue, repo, ex, run_repository=repo)

    outcome = await worker.abort_runs_by_session("sess-group", "bot-A")

    assert outcome.aborted_run_ids == []
    assert outcome.had_terminal is False  # bot-A 自己在该 session 无终态记录
    # bot-B RUNNING run 不受影响
    assert queue.get_by_run_id(run_b).status == "RUNNING"
    # bot-A PENDING 也不动
    assert queue.get_by_run_id(_run_a).status == "PENDING"


async def test_abort_runs_by_session_had_terminal_narrowed_to_target_bot(repo, queue):
    """410 维度收窄到目标 bot：其它 bot 的终态记录不影响该 bot 的 410 判定。"""
    # bot-B 在该 session 有一条 DONE 记录
    run_b = _insert_with_session(repo, queue, "bot-B", "sess-group")
    claimed_b = queue.claim_pending_by_bot("bot-B", "wB", candidates=5)
    assert claimed_b is not None and claimed_b.run_id == run_b
    assert queue.mark_done(run_b, "wB") == 1

    ex = ResultGuardExecutor(_CompletingExecutor(repo), repo)
    worker = _worker(queue, repo, ex, run_repository=repo)

    # abort bot-A：bot-A 无任何记录 -> aborted=false, had_terminal=False（不被 bot-B 的 DONE 影响）
    outcome = await worker.abort_runs_by_session("sess-group", "bot-A")
    assert outcome.aborted_run_ids == []
    assert outcome.had_terminal is False

    # abort bot-B：bot-B 有 DONE 记录 -> had_terminal=True（410）
    outcome_b = await worker.abort_runs_by_session("sess-group", "bot-B")
    assert outcome_b.aborted_run_ids == []
    assert outcome_b.had_terminal is True


async def test_abort_runs_by_session_calls_repo_with_bot_session_args(repo, queue):
    """repository 调用收窄到 (session_id, bot_id) 维度（spy 模式断言调用参数）。"""
    run_id = _insert_with_session(repo, queue, "bot-1", "sess-abort")
    claimed = queue.claim_pending_by_bot("bot-1", "worker-1", candidates=5)
    assert claimed is not None and claimed.run_id == run_id

    ex = ResultGuardExecutor(_CompletingExecutor(repo), repo)
    worker = _worker(queue, repo, ex, run_repository=repo)

    find_running_calls: list[tuple[str, str]] = []
    find_terminal_calls: list[tuple[str, str]] = []
    orig_running = worker._queue.find_running_by_bot_session
    orig_terminal = worker._queue.find_terminal_by_bot_session

    def spy_running(session_id: str, bot_id: str):
        find_running_calls.append((session_id, bot_id))
        return orig_running(session_id, bot_id)

    def spy_terminal(session_id: str, bot_id: str):
        find_terminal_calls.append((session_id, bot_id))
        return orig_terminal(session_id, bot_id)

    worker._queue.find_running_by_bot_session = spy_running  # type: ignore[assignment]
    worker._queue.find_terminal_by_bot_session = spy_terminal  # type: ignore[assignment]

    outcome = await worker.abort_runs_by_session("sess-abort", "bot-1")

    assert outcome.aborted_run_ids == [run_id]
    assert find_running_calls == [("sess-abort", "bot-1")]
    # 有可取消 run 时不调用 find_terminal_by_bot_session
    assert find_terminal_calls == []


async def test_abort_poll_loop_awaits_engine_notifier(repo, queue):
    """_abort_poll_loop 感知 abort 信号后会 await engine_abort_notifier。"""
    run_id = _insert_with_session(repo, queue, "bot-1", "sess-abort")
    record = queue.get_by_run_id(run_id)

    notified = asyncio.Event()
    captured: dict[str, str | None] = {}

    async def engine_notifier(session_id: str, rid: str | None) -> None:
        captured["session_id"] = session_id
        captured["run_id"] = rid
        notified.set()

    worker = _worker(
        queue,
        repo,
        _CompletingExecutor(repo),
        worker_id="worker-1",
        config=BotRequestWorkerConfig(abort_poll_interval_seconds=0.05),
        engine_abort_notifier=engine_notifier,
    )

    run_cancelled = asyncio.Event()

    async def _fake_run():
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            run_cancelled.set()
            raise

    run_task = asyncio.create_task(_fake_run())
    poll_task = asyncio.create_task(worker._abort_poll_loop(record, run_task))

    await asyncio.sleep(0.1)
    queue.update_meta(run_id, {"abort_requested": True})

    await asyncio.wait_for(poll_task, timeout=2)
    await asyncio.wait_for(run_cancelled.wait(), timeout=2)

    assert notified.is_set(), "engine notifier must be awaited"
    assert captured["session_id"] == "sess-abort"
    assert captured["run_id"] == run_id


async def test_bot_runner_abort_propagates_to_bot_service(repo, queue):
    """BotRunner.abort 解析 binding、选择 service 并调用 service.abort。"""
    run_id = _insert_with_session(repo, queue, "bot-1", "sess-abort")
    repo.update_session_id(run_id, "agent:main:sess-real")

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
        ):
            from secbaas.community.spi.bot_service import BotBindingData

            return BotBindingData(
                bot_id="bot-1",
                owner_id="entity-1",
                bot_type="personal",
                engine_type="openclaw",
                binding_id=1,
                device_provider="baas",
                device_id="device-1",
            )

        async def report(self, payload: Any) -> None:
            pass

    from secbaas.community.core.service.bot_run._noop_message_dispatcher import (
        NoopMessageDispatcher,
    )
    from secbaas.community.plugins.eval_env.stub import NoopEvalSessionLog

    runner = BotRunner(
        bot_service_selector=_FakeSelector(),
        run_repository=repo,
        bot_service_plugin=_FakePlugin(),
        binding_resolver=BotBindingResolver(_FakePlugin()),
        dispatchers=[NoopMessageDispatcher()],
        system_config_service=MagicMock(),  # type: ignore[arg-type]
        eval_session_log=NoopEvalSessionLog(),
    )

    await runner.abort(session_id="sess-abort", run_id=run_id)

    assert len(fake_service.calls) == 1
    call = fake_service.calls[0]
    assert call["session_id"] == "agent:main:sess-real"
    assert call["binding_info"].device_id == "device-1"


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


async def test_bot_runner_abort_skips_when_run_id_missing(repo, queue):
    """run_id 为空时直接跳过，不查询 DB。"""
    # 预插记录以确保不会误命中
    _insert_with_session(repo, queue, "bot-1", "sess-skip")
    runner, _ = _make_bot_runner_for_abort(repo)

    await runner.abort(session_id="sess-skip", run_id="")


async def test_bot_runner_abort_skips_when_run_not_found(repo, queue):
    """run_id 不存在时记录日志并返回。"""
    runner, _ = _make_bot_runner_for_abort(repo)
    missing_run_id = uuid4().hex

    await runner.abort(session_id="sess-missing", run_id=missing_run_id)


async def test_bot_runner_abort_skips_when_binding_resolution_fails(repo, queue):
    """binding 解析抛非 NOT_FOUND 异常时，记录日志并返回。"""
    run_id = _insert_with_session(repo, queue, "bot-1", "sess-binding-fail")
    runner, fake_service = _make_bot_runner_for_abort(
        repo,
        binding_return=RuntimeError("binding boom"),
    )

    await runner.abort(session_id="sess-binding-fail", run_id=run_id)

    assert fake_service.calls == []


async def test_bot_runner_abort_skips_when_binding_not_found(repo, queue):
    """binding 返回 NOT_FOUND 时，记录日志并返回。"""
    run_id = _insert_with_session(repo, queue, "bot-1", "sess-binding-none")
    from secbaas.community.api.device_manage import ErrorCode, PaasError

    runner, fake_service = _make_bot_runner_for_abort(
        repo,
        binding_return=PaasError(
            code=ErrorCode.NOT_FOUND,
            message="not found",
        ),
    )

    await runner.abort(session_id="sess-binding-none", run_id=run_id)

    assert fake_service.calls == []


async def test_bot_runner_abort_logs_when_service_abort_fails(repo, queue):
    """service.abort 抛异常时，Runner 记录日志不抛出。"""
    run_id = _insert_with_session(repo, queue, "bot-1", "sess-service-fail")
    runner, fake_service = _make_bot_runner_for_abort(
        repo,
        service_abort_side_effect=RuntimeError("service boom"),
    )

    await runner.abort(session_id="sess-service-fail", run_id=run_id)

    assert len(fake_service.calls) == 1


async def test_abort_poll_loop_returns_when_run_task_is_done(repo, queue):
    """run_task 已结束时，poll loop 直接返回。"""
    run_id = _insert_with_session(repo, queue, "bot-1", "sess-poll-done")
    record = queue.get_by_run_id(run_id)
    worker = _worker(
        queue,
        repo,
        _CompletingExecutor(repo),
        worker_id="worker-poll-done",
        config=BotRequestWorkerConfig(abort_poll_interval_seconds=0.05),
    )

    async def short_run() -> None:
        await asyncio.sleep(0.01)

    run_task = asyncio.create_task(short_run())
    poll_task = asyncio.create_task(worker._abort_poll_loop(record, run_task))

    await asyncio.wait_for(poll_task, timeout=2)


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


# ==================== Tests: scan lock / tick cap / post-run ====================


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
