"""
Cron 轮询服务 - 替代 webhook 回调

定期查询任务执行历史，根据执行记录发送通知
"""

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Callable, Optional

from engine.community.plugin_api.cron.models import CronJob, CronRunRecord
from engine.community.core.cron.protocol import CronService

log = logging.getLogger("new_ocb.cron-polling")


@dataclass
class PolledRun:
    """轮询到的执行记录"""

    job_id: str
    job_name: str
    run_record: CronRunRecord


class CronPollingService:
    """Cron 轮询服务"""

    # 限制并发通知数量，避免过多 TCP 连接
    MAX_CONCURRENT_NOTIFICATIONS = 10
    # 后台通知队列最大长度（防止内存无限增长）
    MAX_PENDING_NOTIFICATIONS = 100

    def __init__(
        self,
        engine: str,
        cron_api: CronService,
        poll_interval_secs: int = 30,
        notify_callback: Optional[Callable[[CronJob, CronRunRecord], Any]] = None,
    ):
        self.engine = engine
        self._cron_api = cron_api
        self.poll_interval = poll_interval_secs
        self.notify_callback = notify_callback
        self._last_run_times: dict[str, int] = {}
        self._running = False
        self._task: Optional[asyncio.Task] = None
        # 信号量限制并发通知数量
        self._notify_semaphore = asyncio.Semaphore(self.MAX_CONCURRENT_NOTIFICATIONS)
        # 限制同时查询执行历史的并发数（避免过多 TCP 连接）
        self._check_semaphore = asyncio.Semaphore(10)
        # 后台通知任务集合（用于限制队列大小）
        self._pending_notifications: set[asyncio.Task] = set()

    async def start(self):
        """启动轮询服务"""
        self._running = True

        # 初始化：获取当前所有任务的最新执行时间，避免重启后重复通知
        await self._init_last_run_times()

        self._task = asyncio.create_task(self._poll_loop())
        log.info(
            f"[CronPolling] 启动轮询服务，引擎={self.engine}, 间隔={self.poll_interval}秒"
        )

    async def _init_last_run_times(self):
        """初始化 _last_run_times，获取当前所有任务的最新执行时间"""
        try:
            jobs = await self._cron_api.list_jobs()
            for job in jobs:
                # 只处理启用通知的任务
                if not job.notify or not job.notify.enabled:
                    continue

                try:
                    runs = await self._cron_api.get_runs(job.id, limit=1)
                    if runs:
                        # 记录最新的执行时间
                        latest_run = max(runs, key=lambda r: r.finished_at_ms)
                        self._last_run_times[job.id] = latest_run.finished_at_ms
                        log.debug(
                            f"[CronPolling] 初始化 {job.id}: last_run={latest_run.finished_at_ms}"
                        )
                except Exception as e:
                    log.warning(f"[CronPolling] 初始化任务 {job.id} 执行时间失败: {e}")

            log.info(
                f"[CronPolling] 已初始化 {len(self._last_run_times)} 个任务的执行时间"
            )
        except Exception as e:
            log.warning(f"[CronPolling] 初始化执行时间失败: {e}")

    async def stop(self, graceful: bool = True):
        """停止轮询服务

        Args:
            graceful: 是否优雅停止。True=等待当前轮询周期完成，False=立即取消
        """
        self._running = False

        if self._task:
            if graceful:
                # 优雅停止：等待当前轮询完成（最多5秒）
                try:
                    await asyncio.wait_for(self._task, timeout=5.0)
                except asyncio.TimeoutError:
                    log.warning("[CronPolling] 优雅停止超时，强制取消")
                    self._task.cancel()
                    try:
                        await self._task
                    except asyncio.CancelledError:
                        pass
            else:
                # 强制停止
                self._task.cancel()
                try:
                    await self._task
                except asyncio.CancelledError:
                    pass

        log.info("[CronPolling] 停止轮询服务")

    async def _poll_loop(self):
        """轮询主循环"""
        while self._running:
            try:
                await self._check_all_jobs()
            except Exception as e:
                log.exception(f"[CronPolling] 轮询异常: {e}")

            await asyncio.sleep(self.poll_interval)

    async def _check_all_jobs(self):
        """检查所有任务的执行状态（带超时保护）"""
        # 1. 获取所有任务
        try:
            jobs = await asyncio.wait_for(self._cron_api.list_jobs(), timeout=10.0)
        except asyncio.TimeoutError:
            log.error("[CronPolling] 获取任务列表超时")
            return
        except Exception as e:
            log.error(f"[CronPolling] 获取任务列表失败: {e}")
            return

        log.info(f"[CronPolling] 获取任务列表: {jobs}")

        # 2. 并发检查所有任务（带超时）
        tasks = []
        for job in jobs:
            if not job.notify or not job.notify.enabled:
                continue
            # 为每个任务创建独立的检查任务
            tasks.append(self._check_single_job(job))

        if tasks:
            # 并发执行，最多等待 25 秒（避免超过轮询间隔）
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    log.warning(f"[CronPolling] 任务检查异常: {result}")

    def _cleanup_pending_notifications(self):
        """清理已完成的通知任务，防止内存泄漏"""
        done_tasks = [t for t in self._pending_notifications if t.done()]
        for task in done_tasks:
            self._pending_notifications.discard(task)

    async def _check_single_job(self, job: CronJob):
        """检查单个任务的执行状态（带超时、限流）"""
        # 使用信号量限制并发查询数量
        async with self._check_semaphore:
            try:
                # 获取执行历史（5秒超时）
                runs = await asyncio.wait_for(
                    self._cron_api.get_runs(job.id, limit=5), timeout=5.0
                )
            except asyncio.TimeoutError:
                log.warning(f"[CronPolling] 获取任务 {job.id} 执行历史超时")
                return
            except Exception as e:
                log.warning(f"[CronPolling] 获取任务 {job.id} 执行历史失败: {e}")
                return

        # 检查新的执行记录（在信号量外执行，避免持有锁时间过长）
        last_known = self._last_run_times.get(job.id, 0)

        # 先收集所有需要通知的新执行记录
        new_runs = [run for run in runs if run.finished_at_ms > last_known]

        if not new_runs:
            return

        # 清理已完成的任务，检查队列空间
        self._cleanup_pending_notifications()
        available_slots = self.MAX_PENDING_NOTIFICATIONS - len(
            self._pending_notifications
        )

        # 如果队列已满，丢弃旧的通知任务腾出空间
        if available_slots < len(new_runs):
            needed = len(new_runs) - available_slots
            cancelled = 0
            for old_task in list(self._pending_notifications):
                if not old_task.done():
                    old_task.cancel()
                    cancelled += 1
                    if cancelled >= needed:
                        break
            if cancelled > 0:
                log.warning(
                    f"[CronPolling] 队列已满，取消 {cancelled} 个旧通知任务以腾出空间"
                )
            # 清理被取消的任务
            self._cleanup_pending_notifications()
            available_slots = self.MAX_PENDING_NOTIFICATIONS - len(
                self._pending_notifications
            )

        # 只处理能容纳的新记录（超出部分丢弃）
        for i, run in enumerate(new_runs):
            if i >= available_slots:
                log.warning(
                    f"[CronPolling] 队列已满，丢弃新通知: job={job.id}, finished_at={run.finished_at_ms}"
                )
                # 仍然更新时间戳避免重复检查
                if run.finished_at_ms > self._last_run_times.get(job.id, 0):
                    self._last_run_times[job.id] = run.finished_at_ms
                continue

            # 创建通知任务并跟踪
            task = asyncio.create_task(self._process_run_safe(job, run))
            self._pending_notifications.add(task)

            # 立即更新时间戳，避免重复通知（即使发送失败）
            if run.finished_at_ms > self._last_run_times.get(job.id, 0):
                self._last_run_times[job.id] = run.finished_at_ms

    async def _process_run_safe(self, job: CronJob, run: CronRunRecord):
        """安全地处理执行记录（带超时、限流，不阻塞轮询）"""
        try:
            # 使用信号量限制并发通知数量
            async with self._notify_semaphore:
                try:
                    await asyncio.wait_for(self._process_run(job, run), timeout=10.0)
                except asyncio.TimeoutError:
                    log.warning(f"[CronPolling] 处理执行记录超时: job={job.id}")
                except Exception as e:
                    log.error(
                        f"[CronPolling] 处理执行记录异常: job={job.id}, error={e}"
                    )
        except asyncio.CancelledError:
            # 任务被取消（队列溢出时），记录日志
            log.warning(f"[CronPolling] 通知任务被取消（队列溢出）: job={job.id}")
            raise  # 重新抛出以正确传播取消状态

    async def _process_run(self, job: CronJob, run: CronRunRecord):
        """处理单个执行记录（发送通知）"""
        log.info(
            f"[CronPolling] 新执行记录: job={job.id}, status={run.status}, duration={run.duration_ms}ms"
        )

        # 调用通知回调（由当前 profile 的通知插件实现）
        if self.notify_callback:
            try:
                await self.notify_callback(job, run)
                log.info(f"[CronPolling] 已触发通知回调: job={job.id}")
            except Exception as e:
                log.error(f"[CronPolling] 通知回调失败: {e}")
