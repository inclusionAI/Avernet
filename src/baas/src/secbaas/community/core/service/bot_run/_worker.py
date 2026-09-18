"""Bot 请求队列 Worker（阶段一）。

每台机器运行的 Worker：轮询 ``baas_bot_run_queue`` 里 PENDING 的工作项，按 bot
维度做全局并发限流后无锁认领（claim），再交给注入的 ``RequestExecutor`` 执行。
Worker 只负责"发现 → 限流 → 认领 → 并发控制 → 心跳 → 队列终态标记"；
真正的 binding 解析 / 建会话 / 发消息 / 写结果（落 ``baas_bot_run``），
以及 session 串行锁，由 executor 负责（见增量 4/5/6）。

双表：队列工作项在 ``baas_bot_run_queue``（瞬态、高频 churn），结果正文在
``baas_bot_run``（持久、被 GET /runs 读）。Worker 不直接写结果表。

并发互斥不依赖 ``SKIP LOCKED``：claim 用条件 UPDATE 的 affected-rows 实现行级
出队互斥（见 OrmBotRunQueueRepository.claim_pending_by_bot），因此多机可同时
拉取，无需把某 bot 路由到固定机器。
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import socket
import time
import uuid
from collections.abc import Awaitable, Callable, Generator
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from secbaas.community.core.repository.bot_run import BotRunRepository
from secbaas.community.core.repository.bot_run_queue import (
    BotRunQueueRecord,
    BotRunQueueRepository,
)
from secbaas.community.logger import get_logger
from secbaas.community.tracer import get_tracer_plugin

from ._bot_concurrency import BotConcurrencyManager
from ._executor import RequeuedToPendingError
from ._internal_protocols import (
    AbortOutcome,
    PostRunCallback,
    RequestExecutor,
)

if TYPE_CHECKING:
    from secbaas.community.core.service.distributed_lock import DistributedLockService

logger = get_logger("core-bot-run")


@contextlib.contextmanager
def _trace_context_from_meta(meta: dict[str, Any] | None) -> Generator[None]:
    """从队列工作项 meta 中恢复 trace context。

    1. extract_context 从 meta["traceparent"] 反序列化 trace context
    2. start_span 创建 child span，通过 child_of 挂到提取出的 parent context
    退出时自动关闭 span。

    不使用 attach_context/detach_context：SOFA 的 attach_context 期望的是
    scope 对象（带 .span），而 extract_context 返回的是 SofaSpanContext，
    类型不匹配会导致下游取 scope.span 时 AttributeError。
    """
    tracer = get_tracer_plugin()
    carrier = (meta or {}).get("traceparent") or {}
    trace_ctx = tracer.extract_context(carrier)
    with tracer.start_span("bot_queue_worker.execute", child_of=trace_ctx):
        yield


@dataclass
class BotRequestWorkerConfig:
    """Worker 运行参数。"""

    enabled: bool = True
    poll_interval_seconds: float = 1
    discover_limit: int = (
        200  # 每轮发现的 bot 数上限（活跃 bot 超过该值时需上轮转游标）
    )
    candidates_per_bot: int = 5  # 每个 bot 单次认领的候选数
    max_concurrent: int = 50  # 单 Worker 最大并发执行数
    heartbeat_interval_seconds: float = 30.0
    timeout_scan_interval_seconds: float = 5.0  # 超时扫描间隔
    stale_heartbeat_seconds: float = 120.0  # 心跳过期阈值（判定对端 worker 已 down）
    cap_warn_interval_seconds: float = 30.0  # 全局并发上限拦截日志的节流间隔
    timeout_scan_lock_name: str = "bot_run_timeout_scan_lock"  # 全局超时扫描单飞锁
    timeout_scan_lock_expire_seconds: int = 60  # 单飞锁过期时间（秒）
    abort_poll_interval_seconds: float = 1.0  # abort 信号轮询间隔


#: Engine 通知回调类型：``chat.abort`` best-effort 通知 engine 取消 session/run。
#: 失败仅记录日志，不影响 abort 主流程。``session_id`` 用于 engine 侧 session 定位，
#: ``run_id`` 为本次取消的 run（非 None 时通知 engine 取消该 run）。
EngineAbortNotifier = Callable[[str, str | None], Awaitable[None]]


def _default_worker_id() -> str:
    return f"{socket.gethostname()}_{os.getpid()}_{uuid.uuid4().hex[:8]}"


class BotRequestWorker:
    """按 bot 维度认领队列工作项并执行的 Worker。"""

    def __init__(
        self,
        queue_repository: BotRunQueueRepository,
        qpm_manager: BotConcurrencyManager,
        executor: RequestExecutor,
        *,
        run_repository: BotRunRepository,
        lock_service: DistributedLockService,
        post_run_callback_factories: dict[str, PostRunCallback],
        config: BotRequestWorkerConfig,
        engine_abort_notifier: EngineAbortNotifier | None = None,
        worker_id: str | None = None,
    ) -> None:
        self._queue = queue_repository
        self._qpm = qpm_manager
        self._executor = executor
        # 结果正文仓库（baas_bot_run），用于 abort/超时回收时写未终结 run 终态。
        self._run_repository = run_repository
        # best-effort 通知 engine 取消 session/run（BotWebsocketClient.chat_abort seam）。
        # DI 未注入时为 None，仅跳过 engine 通知，不影响本机 cancel+force_done+mark_failed。
        self._engine_abort_notifier = engine_abort_notifier
        # callback 名称 -> 已构造的 PostRunCallback 实例（DI 注入）
        self._callback_factories = post_run_callback_factories
        # 全局超时扫描的单飞锁（DI 注入）
        self._lock_service = lock_service
        self._config = config
        self._worker_id = worker_id or _default_worker_id()

        self._active = 0
        self._last_cap_warn = 0.0
        self._stop_event: asyncio.Event | None = None
        self._loop_task: asyncio.Task[None] | None = None
        self._timeout_task: asyncio.Task[None] | None = None
        # run_id -> 正在执行的 _run_one task，用于超时扫描/abort 时 cancel 本机任务
        self._running_tasks: dict[str, asyncio.Task[None]] = {}

    @property
    def worker_id(self) -> str:
        return self._worker_id

    @property
    def active_count(self) -> int:
        return self._active

    # ----------------------------- 生命周期 -----------------------------

    def _start_sync(self) -> None:
        """在当前运行的事件循环上启动 Worker 主循环。"""
        if self._loop_task is not None and not self._loop_task.done():
            logger.warning("[BotRequestWorker] already running")
            return
        if not self._config.enabled:
            logger.info("[BotRequestWorker] disabled by config, not starting")
            return
        self._stop_event = asyncio.Event()
        self._loop_task = asyncio.create_task(self._run_loop())
        self._timeout_task = asyncio.create_task(self._timeout_scan_loop())
        logger.info(
            "[BotRequestWorker] started worker_id=%s max_concurrent=%s",
            self._worker_id,
            self._config.max_concurrent,
        )

    # -- Lifecycle Protocol --------------------------------------------------

    async def start(self) -> None:
        """Lifecycle.start: start the worker main loop."""
        self._start_sync()

    async def stop(self) -> None:
        """停止主循环（不强杀在执行中的请求，由其自然完成或被恢复机制回收）。"""
        if self._stop_event is not None:
            self._stop_event.set()
        if self._loop_task is not None:
            with contextlib.suppress(asyncio.CancelledError):
                await self._loop_task
            self._loop_task = None
        if self._timeout_task is not None:
            with contextlib.suppress(asyncio.CancelledError):
                await self._timeout_task
            self._timeout_task = None
        logger.info("[BotRequestWorker] stopped worker_id=%s", self._worker_id)

    async def _run_loop(self) -> None:
        assert self._stop_event is not None
        while not self._stop_event.is_set():
            try:
                request_cnt = await self._tick()
                logger.debug("[BotRequestWorker] tick request_cnt=%s", request_cnt)
            except Exception as e:
                logger.exception("[BotRequestWorker] tick error: %s", e)
            await asyncio.sleep(self._config.poll_interval_seconds)

    async def _timeout_scan_loop(self) -> None:
        """独立于 _run_loop 的全局超时对账协程（单飞）。

        只处理「无需本机能力」的两类：PENDING 超时、非本机 stale RUNNING（owner
        已 down）。本机 RUNNING 的超时由各进程的 ``_run_guardian`` 负责（只有 owner
        能 cancel）。整体由分布式锁保证集群内单飞，避免每进程重复扫描放大 DB 压力。
        """
        assert self._stop_event is not None
        interval = self._config.timeout_scan_interval_seconds
        while not self._stop_event.is_set():
            try:
                await self._timeout_scan_once()
            except Exception as e:
                logger.exception("[BotRequestWorker] timeout scan error: %s", e)
            await asyncio.sleep(interval)

    def _is_heartbeat_stale(self, record: BotRunQueueRecord) -> bool:
        """判断 RUNNING 记录的心跳是否已过期（对端 worker 可能已 down）。"""
        if record.last_heartbeat is None:
            return True
        elapsed = (datetime.now() - record.last_heartbeat).total_seconds()
        return elapsed > self._config.stale_heartbeat_seconds

    async def _timeout_scan_once(self) -> None:
        """全局超时对账（单飞）。

        抢到锁的进程才执行，其余进程直接跳过，避免每进程重复扫描。
        """
        with self._lock_service.try_lock(
            lock_name=self._config.timeout_scan_lock_name,
            expire_seconds=self._config.timeout_scan_lock_expire_seconds,
            block=False,
        ) as lock:
            if not lock.acquired:
                logger.debug(
                    "[BotRequestWorker] timeout scan skipped: lock %s held by "
                    "another process",
                    self._config.timeout_scan_lock_name,
                )
                return
            await self._timeout_scan_global()

    async def _timeout_scan_global(self) -> None:
        """对账 PENDING 超时 与 非本机 stale RUNNING。

        本机 RUNNING 的超时由各自的 ``_run_guardian`` 负责（只有 owner 能 cancel），
        这里只处理不需要本机能力的两类：
        - PENDING 超时（从未被认领）：委托 executor 写结果终态后 force_done；
        - RUNNING 且心跳过期（owner 已 down）：写结果终态 + force_done；
          RUNNING 但心跳正常（owner 还活着，含本机）→ 跳过。
        """
        records = self._queue.scan_timeout()
        for record in records:
            if record.status == "RUNNING":
                if not self._is_heartbeat_stale(record):
                    # owner 还活着（含本机），由各自 guardian 处理超时
                    continue
                # owner 已 down：先写结果终态再 force_done。否则队列置 DONE 后
                # recovery/reset_stale_running 不再命中（按 RUNNING 过滤），
                # baas_bot_run 会永远停在 PENDING/RUNNING。update_error 幂等。
                with contextlib.suppress(Exception):
                    self._run_repository.update_error(
                        record.run_id, "worker lost (stale heartbeat)"
                    )
                with contextlib.suppress(Exception):
                    self._queue.force_done(record.run_id)
                logger.warning(
                    "[BotRequestWorker] timeout scan: run_id=%s status=%s "
                    "stale heartbeat (worker=%s), force done",
                    record.run_id,
                    record.status,
                    record.assigned_worker,
                )
            else:
                # PENDING 超时：走 executor 标 FAILED + force_done
                await self._executor.execute(record)
                with contextlib.suppress(Exception):
                    self._queue.force_done(record.run_id)
                logger.warning(
                    "[BotRequestWorker] timeout scan: run_id=%s status=%s marked failed",
                    record.run_id,
                    record.status,
                )
            await self._fire_timeout_callback(record)

    async def _fire_timeout_callback(self, record: BotRunQueueRecord) -> None:
        """best-effort 触发该工作项的 post-run callback（超时/回收路径）。"""
        callback = self._resolve_callback(record)
        if callback is None:
            return
        try:
            await callback(record.run_id)
        except Exception as e:
            logger.error(
                "[BotRequestWorker] timeout scan callback failed run_id=%s: %s",
                record.run_id,
                e,
                exc_info=True,
            )

    # ----------------------------- chat.abort 接入面 -----------------------------

    async def abort_runs_by_session(self, session_id: str, bot_id: str) -> AbortOutcome:
        """按 (bot_id, session_id) 维度取消目标 bot 的 RUNNING run。

        复用 ``_terminate_local_timeout`` 的 cancel+force_done 模板，顺序：
        1. ``run_repository.update_error(run_id, ...)`` 标 FAILED（幂等：已终态时 no-op）；
        2. ``queue.request_abort(run_id)`` 写跨实例信号（持有该 run 的 Worker 通过
           ``_abort_poll_loop`` 轮询感知后 cancel 本机 task）；
        3. ``queue.force_done(run_id)`` 终结队列工作项（幂等）；
        4. 若 ``assigned_worker == 本 worker``，cancel 本机 ``_running_tasks[run_id]``；
        5. best-effort 通知 engine（``engine_abort_notifier``），失败仅记录日志。

        群聊多 bot 共享同一 ``session_id`` 时仅取消目标 bot 的 RUNNING run；PENDING
        不命中（由 ``_timeout_scan_once`` 超时路径兜底）。非本机 RUNNING run 无法本机
        cancel，由 force_done + engine 通知 + 对端超时/心跳兜底（与 timeout 同构）。
        ``update_error`` 与 ``force_done`` 均幂等，abort 与 timeout 并发争抢同一 run
        时靠幂等收敛到同一终态（R1）。

        Returns:
            AbortOutcome: 被取消的 run_id 列表，以及目标 bot 在该 session 下是否存在
            已终结记录（用于 410 vs 200 判定，维度收窄到该 bot）。
        """
        if not session_id or not bot_id:
            return AbortOutcome(aborted_run_ids=[], had_terminal=False)

        records = self._queue.find_running_by_bot_session(session_id, bot_id)
        if not records:
            terminals = self._queue.find_terminal_by_bot_session(session_id, bot_id)
            return AbortOutcome(aborted_run_ids=[], had_terminal=bool(terminals))

        aborted_run_ids: list[str] = []
        for record in records:
            run_id = record.run_id
            # 1. 写终态（FAILED）—— update_error 仅在 PENDING/RUNNING 时生效，已终态 no-op
            try:
                self._run_repository.update_error(run_id, "aborted by chat.abort")
            except Exception as e:
                logger.error(
                    "[BotRequestWorker] abort update_error failed run_id=%s: %s",
                    run_id,
                    e,
                    exc_info=True,
                )
            # 2. 写跨实例 abort 信号（持有该 run 的 Worker 轮询感知后 cancel 本机 task）
            with contextlib.suppress(Exception):
                self._queue.request_abort(run_id)
            # 3. force_done 终结队列工作项（幂等）
            with contextlib.suppress(Exception):
                self._queue.force_done(run_id)
            # 4. cancel 本机正在执行的 task
            if record.assigned_worker == self._worker_id:
                running_task = self._running_tasks.get(run_id)
                if running_task is not None and not running_task.done():
                    running_task.cancel()
                    logger.info(
                        "[BotRequestWorker] abort cancelled local task run_id=%s",
                        run_id,
                    )
            aborted_run_ids.append(run_id)
            logger.info(
                "[BotRequestWorker] abort run_id=%s session_id=%s bot_id=%s "
                "status=%s worker=%s marked failed+force_done",
                run_id,
                session_id,
                bot_id,
                record.status,
                record.assigned_worker,
            )

        # 5. best-effort 通知 engine（一次 维度通知，run_id 取首个）
        if self._engine_abort_notifier is not None and aborted_run_ids:
            try:
                await self._engine_abort_notifier(session_id, aborted_run_ids[0])
            except Exception as e:
                logger.warning(
                    "[BotRequestWorker] abort engine notify failed session_id=%s "
                    "bot_id=%s: %s",
                    session_id,
                    bot_id,
                    e,
                    exc_info=True,
                )

        return AbortOutcome(aborted_run_ids=aborted_run_ids, had_terminal=False)

    # ----------------------------- 主循环单步 -----------------------------

    async def _tick(self) -> int:
        """执行一轮发现→限流→认领→派发，返回本轮派发的请求数。"""
        if self._active >= self._config.max_concurrent:
            return 0

        bots = self._queue.discover_active_bots(self._config.discover_limit)
        dispatched = 0
        idle_bots: list[str] = []
        for bot_id in bots:
            if self._active >= self._config.max_concurrent:
                logger.info(
                    "[BotRequestWorker] active bots(%s) > max concurrent(%s), skip %s",
                    self._active,
                    self._config.max_concurrent,
                    bot_id,
                )
                break

            max_running = self._qpm.get_concurrency_num(bot_id)
            if max_running is None:
                logger.error(
                    "[BotRequestWorker] bot has no concurrency limit configured, "
                    "skip %s",
                    bot_id,
                )
                continue
            # 全局并发上限由队列层按 RUNNING 在途数强制（claim 时校验 max_running），
            # 跨进程生效；这里只受本机 max_concurrent 与该 bot 全局在途数约束。
            # 尽量多地从该 bot 排空 PENDING（不同请求可并行），避免高并发 bot
            # 被 poll 周期卡成瓶颈。
            claimed_here = 0
            while self._active < self._config.max_concurrent:
                record = self._queue.claim_pending_by_bot(
                    bot_id,
                    self._worker_id,
                    candidates=self._config.candidates_per_bot,
                    max_running=max_running,
                )
                if record is None:
                    break  # 已无可认领的 PENDING，或已达该 bot 全局并发上限
                claimed_here += 1
                self._active += 1
                dispatched += 1
                task = asyncio.create_task(self._run_one(record))
                self._running_tasks[record.run_id] = task
            if claimed_here == 0:
                idle_bots.append(bot_id)

        self._maybe_log_cap(idle_bots)
        return dispatched

    def _maybe_log_cap(self, idle_bots: list[str]) -> list[str]:
        """观测性：本轮「有 PENDING 但一个都没认领到」的 bot 中，若确实是被全局并发
        上限拦住的，按节流打一条 INFO，便于区分「被限流」与「没活了」。

        计数查询只在节流窗口内做一次，不落在每轮热路径上。

        Returns:
            本轮判定为「被全局并发上限拦住」的 bot_id 列表（供观测/测试）。
        """
        if not idle_bots:
            return []
        now = time.monotonic()
        if now - self._last_cap_warn < self._config.cap_warn_interval_seconds:
            return []
        self._last_cap_warn = now
        capped: list[str] = []
        for bot_id in idle_bots:
            max_running = self._qpm.get_concurrency_num(bot_id)
            if max_running is None:
                continue
            try:
                if self._queue.count_running_by_bot(bot_id) >= max_running:
                    capped.append(bot_id)
            except Exception:
                logger.warning(
                    "[BotRequestWorker] count_running_by_bot failed bot_id=%s",
                    bot_id,
                    exc_info=True,
                )
        if capped:
            logger.info(
                "[BotRequestWorker] %d bot(s) throttled by global concurrency cap: %s",
                len(capped),
                capped[:10],
            )
        return capped

    def _resolve_callback(self, record: BotRunQueueRecord) -> PostRunCallback | None:
        """根据 ``record.meta["callback_function"]`` 从 DI 注入的 factories 查找回调实例。"""
        cb_name: str | None = record.meta.get("callback_function")
        if not cb_name:
            return None
        return self._callback_factories.get(cb_name)

    async def _run_one(self, record: BotRunQueueRecord) -> None:
        """包裹单个工作项的执行：后台守护（心跳 + 到点取消）+ 队列终态标记 + 并发计数。

        ``finally`` 只做本地资源清理。``baas_bot_run`` 的业务终态由 executor
        链负责；Worker 只根据 executor 结果推进 ``baas_bot_run_queue``。
        """
        post_run_callback = self._resolve_callback(record)

        run_task = asyncio.current_task()
        guardian = asyncio.create_task(
            self._run_guardian(record, self._record_deadline(record))
        )
        abort_poll_task = (
            asyncio.create_task(self._abort_poll_loop(record, run_task))
            if run_task is not None
            else None
        )
        try:
            with _trace_context_from_meta(record.meta):
                try:
                    await self._executor.execute(record)
                except RequeuedToPendingError as e:
                    await self._requeue_pending(record, e)
                    return

                await self._post_run(record, post_run_callback)
                self._mark_queue_done(record)
        finally:
            self._running_tasks.pop(record.run_id, None)
            guardian.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await guardian
            if abort_poll_task is not None:
                abort_poll_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await abort_poll_task
            self._active -= 1

    @staticmethod
    def _record_deadline(record: BotRunQueueRecord) -> datetime | None:
        """按 ``gmt_create + meta.timeout`` 计算执行截止时间（与 scan_timeout 同源）。

        无 meta.timeout 或 gmt_create 缺失时返回 None（不设 deadline，只做心跳）。
        """
        timeout = record.meta.get("timeout")
        if timeout is None or record.gmt_create is None:
            return None
        return record.gmt_create + timedelta(seconds=float(timeout))

    async def _requeue_pending(
        self,
        record: BotRunQueueRecord,
        error: RequeuedToPendingError,
    ) -> None:
        """session busy 分支：只放回 PENDING，不触发回调，不标 DONE。"""
        release_result: int | None = None
        try:
            release_result = self._queue.release_to_pending(
                record.run_id, self._worker_id
            )
        except Exception as release_error:
            logger.warning(
                "[BotRequestWorker] release_to_pending raised run_id=%s err=%s",
                record.run_id,
                release_error,
            )
        logger.info(
            "[BotRequestWorker] run_id=%s requeued to pending, "
            "release_result=%s skip callback (session=%s busy)",
            record.run_id,
            release_result,
            error.session_id,
        )

    def _mark_queue_done(self, record: BotRunQueueRecord) -> None:
        """非 requeue 分支完成后，按当前 Worker owner fencing 标记队列 DONE。"""
        try:
            result = self._queue.mark_done(record.run_id, self._worker_id)
        except Exception as e:
            logger.warning(
                "[BotRequestWorker] mark_done raised run_id=%s err=%s",
                record.run_id,
                e,
            )
            return
        if result == 0:
            logger.info(
                "[BotRequestWorker] mark_done no-op (already PENDING/DONE) "
                "run_id=%s result=%s",
                record.run_id,
                result,
            )

    async def _post_run(
        self,
        record: BotRunQueueRecord,
        post_run_callback: PostRunCallback | None,
    ) -> None:
        """执行 post_run_callback（仅正常完成或异常兜底时调用）。"""
        if post_run_callback is not None:
            try:
                await post_run_callback(record.run_id)
            except Exception as e:
                logger.error(
                    "[BotRequestWorker] post_run_callback failed run_id=%s: %s",
                    record.run_id,
                    e,
                    exc_info=True,
                )
        else:
            logger.info(
                "[BotRequestWorker] no run callback run_id=%s worker=%s",
                record.run_id,
                self._worker_id,
            )

    async def _abort_poll_loop(
        self, record: BotRunQueueRecord, run_task: asyncio.Task[None]
    ) -> None:
        """轮询 meta 的 ``abort_requested`` 信号，感知后取消本机执行 task。

        供 chat.abort 跨实例通知：``abort_runs_by_session`` 写入
        ``request_abort`` 信号 + force_done 后，实际持有该 run 的 Worker 由
        本循环感知信号并 cancel 本机 task；engine 通知 best-effort，失败
        仅记录日志。run_task 结束（正常完成/超时/被取消）时循环自然退出。
        """
        interval = self._config.abort_poll_interval_seconds
        run_id = record.run_id
        while not run_task.done():
            await asyncio.sleep(interval)
            try:
                if self._queue.get_by_run_id(run_id) is None:
                    continue
                if not self._queue.is_abort_requested(run_id):
                    continue
            except Exception as e:
                logger.warning(
                    "[BotRequestWorker] abort poll failed run_id=%s: %s",
                    run_id,
                    e,
                )
                continue
            logger.info(
                "[BotRequestWorker] abort signal detected, cancelling local task "
                "run_id=%s session_id=%s",
                run_id,
                record.session_id,
            )
            if not run_task.done():
                run_task.cancel()
            with contextlib.suppress(Exception):
                self._queue.force_done(run_id)
            if self._engine_abort_notifier is not None:
                try:
                    await self._engine_abort_notifier(record.session_id, run_id)
                except Exception as e:
                    logger.warning(
                        "[BotRequestWorker] abort engine notify failed run_id=%s: %s",
                        run_id,
                        e,
                        exc_info=True,
                    )
            return

    async def _run_guardian(
        self, record: BotRunQueueRecord, deadline: datetime | None
    ) -> None:
        """单个执行期间的后台守护：周期心跳续约 + 到点终结本机超时 task。

        合并原 heartbeat 与本地超时 watchdog：
        - 周期 ``touch_heartbeat`` 刷新 last_heartbeat，供 recovery 判活（避免活着的
          owner 被误回收）；
        - ``deadline`` 到点则终结本机 task：cancel 执行 task（走 executor 的
          CancelledError → 标结果 FAILED）+ force_done 队列行 + 触发 post-run 回调，
          保证本机超时后队列表/结果表都收敛、回调不丢。``sleep`` 取
          ``min(heartbeat_interval, 剩余时间)`` 保证到点精度。
        """
        interval = self._config.heartbeat_interval_seconds
        while True:
            remaining: float | None = None
            if deadline is not None:
                remaining = (deadline - datetime.now()).total_seconds()
                if remaining <= 0:
                    await self._terminate_local_timeout(record)
                    return
            await asyncio.sleep(
                interval if remaining is None else min(interval, remaining)
            )
            try:
                self._queue.touch_heartbeat(record.run_id, self._worker_id)
            except Exception as e:
                logger.warning(
                    "[BotRequestWorker] heartbeat failed run_id=%s: %s",
                    record.run_id,
                    e,
                )

    async def _terminate_local_timeout(self, record: BotRunQueueRecord) -> None:
        """本机超时终结：cancel 本机 task + force_done 队列行 + 触发回调。

        结果终态（FAILED）由被 cancel 的 executor 链（ResultGuardExecutor 的
        CancelledError 分支）负责；这里补齐队列终态与回调，避免本机超时后队列行
        停在 RUNNING（占并发额度）且回调丢失。force_done/回调均幂等。
        """
        self._cancel_local_task(record.run_id)
        with contextlib.suppress(Exception):
            self._queue.force_done(record.run_id)
        await self._fire_timeout_callback(record)

    def _cancel_local_task(self, run_id: str) -> None:
        """到点取消本机正在执行的 task（若仍在运行）。"""
        task = self._running_tasks.get(run_id)
        if task is not None and not task.done():
            task.cancel()
            logger.warning(
                "[BotRequestWorker] deadline reached, cancelled local task run_id=%s",
                run_id,
            )

    # ----------------------------- 并发限制 -----------------------------
