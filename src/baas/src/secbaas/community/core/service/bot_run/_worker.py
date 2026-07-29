"""Bot 请求队列 Worker（阶段一）。

每台机器运行的 Worker：轮询 ``baas_bot_run_queue`` 里 PENDING 的工作项，按 bot
维度做 QPM 限流后无锁认领（claim），再交给注入的 ``RequestExecutor`` 执行。
Worker 只负责"发现 → 限流 → 认领 → 并发控制 → 心跳 → 终态标记"；真正的
binding 解析 / 建会话 / 发消息 / 写结果（落 ``baas_bot_run``），以及 session
串行锁，由 executor 负责（见增量 4/5/6）。

双表：队列工作项在 ``baas_bot_run_queue``（瞬态、高频 churn），结果正文在
``baas_bot_run``（持久、被 GET /runs 读）。Worker 主要操作队列仓库，仅在
executor 异常的兜底路径写 ``baas_bot_run`` 的 FAILED 终态。

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
from collections.abc import Generator
from dataclasses import dataclass
from typing import Any

from secbaas.community.core.repository.bot_run import BotRunRepository
from secbaas.community.core.repository.bot_run_queue import (
    BotRunQueueRecord,
    BotRunQueueRepository,
)
from secbaas.community.logger import get_logger
from secbaas.community.tracer import get_tracer_plugin

from ._bot_concurrency import (
    BotConcurrencyManager,
    ConcurrencyLimiter,
    FixedMachineCountProvider,
)
from ._executor import RequeuedToPendingError
from ._internal_protocols import (
    MachineCountProvider,
    PostRunCallback,
    RequestExecutor,
)

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
    discover_limit: int = 50  # 每轮发现的 bot 数上限
    candidates_per_bot: int = 5  # 每个 bot 单次认领的候选数
    max_concurrent: int = 50  # 单 Worker 最大并发执行数
    heartbeat_interval_seconds: float = 30.0
    timeout_scan_interval_seconds: float = 5.0  # 超时扫描间隔
    bucket_sweep_interval_seconds: float = 300.0  # 空闲桶扫描间隔
    bucket_idle_ttl_seconds: float = 600.0  # 空闲桶淘汰 TTL


def _default_worker_id() -> str:
    return f"{socket.gethostname()}_{os.getpid()}_{uuid.uuid4().hex[:8]}"


class BotRequestWorker:
    """按 bot 维度认领队列工作项并执行的 Worker。"""

    def __init__(
        self,
        queue_repository: BotRunQueueRepository,
        run_repository: BotRunRepository,
        qpm_manager: BotConcurrencyManager,
        executor: RequestExecutor,
        *,
        post_run_callback_factories: dict[str, PostRunCallback] | None = None,
        machine_count_provider: MachineCountProvider | None = None,
        config: BotRequestWorkerConfig | None = None,
        worker_id: str | None = None,
    ) -> None:
        self._queue = queue_repository
        self._run = run_repository  # 仅兜底写 baas_bot_run 的 FAILED 终态
        self._qpm = qpm_manager
        self._executor = executor
        # callback 名称 -> 已构造的 PostRunCallback 实例（DI 注入）
        self._callback_factories = post_run_callback_factories or {}
        self._machines = machine_count_provider or FixedMachineCountProvider(1)
        self._config = config or BotRequestWorkerConfig()
        self._worker_id = worker_id or _default_worker_id()

        # bot_id -> (ConcurrencyLimiter, (qpm, machine_count)) 缓存，参数变化时重建
        self._buckets: dict[str, tuple[ConcurrencyLimiter, tuple[int, int]]] = {}
        self._last_bucket_sweep = time.monotonic()
        self._active = 0
        self._stop_event: asyncio.Event | None = None
        self._loop_task: asyncio.Task[None] | None = None
        self._timeout_task: asyncio.Task[None] | None = None

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
            "[BotRequestWorker] started worker_id=%s machine_count=%s max_concurrent=%s",
            self._worker_id,
            self._machines.get_machine_count(),
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
        """独立于 _run_loop 的超时扫描协程。

        定期扫描 PENDING/RUNNING 中已超时的工作项，直接标记失败并终结，
        不依赖 executor 是否拿到锁——解决拿不到 session 锁时 death loop
        导致超时任务无法及时终止的问题。
        """
        assert self._stop_event is not None
        interval = self._config.timeout_scan_interval_seconds
        while not self._stop_event.is_set():
            try:
                await self._timeout_scan_once()
            except Exception as e:
                logger.exception("[BotRequestWorker] timeout scan error: %s", e)
            await asyncio.sleep(interval)

    async def _timeout_scan_once(self) -> None:
        """扫描一轮超时工作项并标记失败，触发 post_run_callback。

        幂等：如果 baas_bot_run 已经是终态（executor 先超时或先完成），
        则 _safety_mark_failed 是 no-op，且跳过 callback（已由 executor
        路径触发过）。
        """
        records = self._queue.scan_timeout()
        for record in records:
            marked = self._safety_mark_failed(record.run_id, "time out")
            with contextlib.suppress(Exception):
                self._queue.force_done(record.run_id)
            if marked:
                logger.warning(
                    "[BotRequestWorker] timeout scan: run_id=%s marked failed",
                    record.run_id,
                )
                callback = self._resolve_callback(record)
                if callback is not None:
                    try:
                        await callback(record.run_id)
                    except Exception as e:
                        logger.error(
                            "[BotRequestWorker] timeout scan callback failed "
                            "run_id=%s: %s",
                            record.run_id,
                            e,
                            exc_info=True,
                        )
            else:
                logger.info(
                    "[BotRequestWorker] timeout scan: run_id=%s already terminal, "
                    "skip callback",
                    record.run_id,
                )

    # ----------------------------- 主循环单步 -----------------------------

    async def _tick(self) -> int:
        """执行一轮发现→限流→认领→派发，返回本轮派发的请求数。"""
        if self._active >= self._config.max_concurrent:
            return 0

        bots = self._queue.discover_active_bots(self._config.discover_limit)
        dispatched = 0
        for bot_id in bots:
            if self._active >= self._config.max_concurrent:
                logger.info(
                    "[BotRequestWorker] active bots(%s) > max concurrent(%s), skip %s",
                    self._active,
                    self._config.max_concurrent,
                    bot_id,
                )
                break

            bucket = self._get_or_create_limiter(bot_id)
            if bucket is None:
                logger.error(
                    "[BotRequestWorker] create bucket fail, must set qpm for %s", bot_id
                )
                continue
            # 在并发与 QPM 预算内，尽量多地从该 bot 排空 PENDING（不同请求可并行），
            # 而非每轮每 bot 只放一个，避免高 QPM bot 被 poll 周期卡成瓶颈。
            while self._active < self._config.max_concurrent and bucket.has_slot():
                record = self._queue.claim_pending_by_bot(
                    bot_id,
                    self._worker_id,
                    candidates=self._config.candidates_per_bot,
                )
                if record is None:
                    break  # 该 bot 已无可认领的 PENDING
                bucket.try_acquire()
                self._active += 1
                dispatched += 1
                asyncio.create_task(self._run_one(record))

        self._sweep_idle_buckets()
        return dispatched

    @staticmethod
    def _is_time_out(record: BotRunQueueRecord) -> bool:
        timeout: int | None = record.meta.get("timeout")
        if timeout is None or record.gmt_create is None:
            return False
        deadline = record.gmt_create.timestamp() + timeout
        return time.time() > deadline

    def _resolve_callback(self, record: BotRunQueueRecord) -> PostRunCallback | None:
        """根据 ``record.meta["callback_function"]`` 从 DI 注入的 factories 查找回调实例。"""
        cb_name: str | None = record.meta.get("callback_function")
        if not cb_name:
            return None
        return self._callback_factories.get(cb_name)

    async def _run_one(self, record: BotRunQueueRecord) -> None:
        """包裹单个工作项的执行：心跳续约 + 兜底异常处理 + 终态标记 + 并发计数。"""
        post_run_callback = self._resolve_callback(record)

        heartbeat = asyncio.create_task(self._heartbeat_loop(record.run_id))
        with _trace_context_from_meta(record.meta):
            try:
                if self._is_time_out(record):
                    raise TimeoutError(
                        f"[BotRequestWorker] time out, run_id={record.run_id}"
                    )
                await self._executor.execute(record)
            except RequeuedToPendingError as e:
                # session 锁被占用，工作项已放回 PENDING 等待重试。
                # 跳过 post_run_callback；mark_done 在 finally 统一执行（此时是 no-op）。
                logger.info(
                    "[BotRequestWorker] run_id=%s requeued to pending, skip callback "
                    "(session=%s busy)",
                    record.run_id,
                    e.session_id,
                )
            except TimeoutError:
                logger.warning("[BotRequestWorker] time out, run_id=%s", record.run_id)
                self._safety_mark_failed(record.run_id, "time out")
                await self._post_run(record, post_run_callback)
            except Exception as e:
                # executor 不应让异常逃逸；逃逸即视为执行 bug。兜底把 baas_bot_run
                # 标记 FAILED，避免该 run 永远停在非终态被客户端轮询（毒消息）。
                logger.error(
                    "[BotRequestWorker] executor raised for run_id=%s: %s",
                    record.run_id,
                    e,
                    exc_info=True,
                )
                self._safety_mark_failed(record.run_id, str(e))
                await self._post_run(record, post_run_callback)
            else:
                # 正常执行完成。但如果 timeout scan 已经在此期间把
                # baas_bot_run 标记为终态（FAILED/TIME_OUT）并触发过 callback，
                # 则跳过本次 callback（避免重复触发）。
                current = self._run.get_by_run_id(record.run_id)
                if current is not None and current.status in ("FAILED", "TIME_OUT"):
                    logger.info(
                        "[BotRequestWorker] run_id=%s already %s (timeout scan?), "
                        "skip post_run callback",
                        record.run_id,
                        current.status,
                    )
                else:
                    await self._post_run(record, post_run_callback)
            finally:
                # 统一标记队列工作项 DONE：仅当仍 RUNNING 时生效。
                # RequeuedToPending 路径已放回 PENDING，此处是 no-op。
                mark_done_exc: Exception | None = None
                mark_done_result: int = -999
                try:
                    mark_done_result = self._queue.mark_done(record.run_id)
                except Exception as e:
                    mark_done_exc = e
                if mark_done_exc is not None:
                    logger.warning(
                        "[BotRequestWorker] mark_done raised run_id=%s err=%s",
                        record.run_id,
                        mark_done_exc,
                    )
                elif mark_done_result == 0:
                    logger.info(
                        "[BotRequestWorker] mark_done no-op (already PENDING/DONE) "
                        "run_id=%s result=%s",
                        record.run_id,
                        mark_done_result,
                    )

                heartbeat.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await heartbeat
                self._active -= 1
                # 归还该 bot 的并发槽位
                cached = self._buckets.get(record.bot_id)
                if cached is not None:
                    cached[0].release()

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

    async def _heartbeat_loop(self, run_id: str) -> None:
        """执行期间周期刷新队列工作项的 last_heartbeat，供宕机恢复判活。"""
        interval = self._config.heartbeat_interval_seconds
        while True:
            await asyncio.sleep(interval)
            try:
                self._queue.touch_heartbeat(run_id)
            except Exception as e:
                logger.warning(
                    "[BotRequestWorker] heartbeat failed run_id=%s: %s", run_id, e
                )

    def _safety_mark_failed(self, run_id: str, error: str) -> bool:
        """将 baas_bot_run 标记为 FAILED（仅当仍 PENDING/RUNNING）。

        返回 True 表示本次标记生效，False 表示已是终态（no-op）。
        """
        try:
            current = self._run.get_by_run_id(run_id)
            if current is not None and current.status in ("PENDING", "RUNNING"):
                self._run.update_error(run_id, f"worker safety-net: {error}")
                return True
        except Exception as e:
            logger.error(
                "[BotRequestWorker] safety mark failed run_id=%s: %s", run_id, e
            )
        return False

    # ----------------------------- 并发限制器 -----------------------------

    def _sweep_idle_buckets(self) -> None:
        """淘汰长时间空闲且无在执行请求的桶，防止 bot_id 过多导致内存泄漏。"""
        now = time.monotonic()
        if now - self._last_bucket_sweep < self._config.bucket_sweep_interval_seconds:
            return
        self._last_bucket_sweep = now
        evicted = [
            bot_id
            for bot_id, (limiter, _) in self._buckets.items()
            if limiter.ref_count == 0
            and now - limiter.last_used > self._config.bucket_idle_ttl_seconds
        ]
        for bot_id in evicted:
            del self._buckets[bot_id]
        if evicted:
            logger.info(
                "[BotRequestWorker] swept %d idle bucket(s), remaining=%d",
                len(evicted),
                len(self._buckets),
            )
        logger.info("[BotRequestWorker] buckets size %s", len(self._buckets))

    def _get_or_create_limiter(self, bot_id: str) -> ConcurrencyLimiter | None:
        qpm = self._qpm.get_concurrency_num(bot_id)
        if qpm is None:
            return None
        machines = self._machines.get_machine_count()
        params = (qpm, machines)

        cached = self._buckets.get(bot_id)
        if cached is not None and cached[1] == params:
            return cached[0]

        # 均分策略：本机并发上限 = qpm / 机器数（至少 1）
        per_machine = max(1, qpm // max(1, machines))
        limiter = ConcurrencyLimiter(capacity=per_machine)
        self._buckets[bot_id] = (limiter, params)
        return limiter
