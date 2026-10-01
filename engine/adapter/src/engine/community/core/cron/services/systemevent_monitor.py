"""
SystemEvent 任务轮询替换服务

定期轮询检查，发现 systemEvent 类型任务时，自动删除并用 agentTurn 替换。
生命周期由 EngineManager 管理。
"""

import asyncio
import logging
from dataclasses import dataclass
from typing import Optional

from engine.community.plugin_api.cron.models import CronJob, CreateJobRequest
from engine.community.core.cron.protocol import CronService

log = logging.getLogger("new_ocb.cron-systemevent")


@dataclass
class _MonitoredJob:
    """已监控的任务记录"""
    job_id: str
    replaced: bool = False


class SystemEventMonitorService:
    """SystemEvent 任务监控替换服务"""

    def __init__(
        self,
        engine: str,
        cron_api: CronService,
        poll_interval_secs: int = 60,
        default_timeout_secs: int = 86400,
        default_model: Optional[str] = None,
    ):
        self.engine = engine
        self._cron_api = cron_api
        self.poll_interval = poll_interval_secs
        self.default_timeout_secs = default_timeout_secs
        self.default_model = default_model
        self._monitored_jobs: dict[str, _MonitoredJob] = {}
        self._running = False
        self._task: Optional[asyncio.Task] = None

    async def start(self):
        """启动监控服务"""
        log.info(f"[SystemEventMonitor] start() called, engine={self.engine}")
        self._running = True

        # 初始化：获取当前所有任务，记录已存在的 systemEvent 任务
        await self._init_monitored_jobs()

        self._task = asyncio.create_task(self._monitor_loop())
        log.info(
            f"[SystemEventMonitor] 启动监控服务，引擎={self.engine}, "
            f"轮询间隔={self.poll_interval}秒"
        )

    async def _init_monitored_jobs(self):
        """初始化已监控任务列表"""
        log.info("[SystemEventMonitor] _init_monitored_jobs() called")
        try:
            jobs = await self._cron_api.list_jobs()
            log.info(f"[SystemEventMonitor] Found {len(jobs)} total jobs")
            for job in jobs:
                # 只记录 systemEvent 类型的任务
                if job.payload.get('kind') == 'systemEvent':
                    self._monitored_jobs[job.id] = _MonitoredJob(job_id=job.id)
                    log.info(f"[SystemEventMonitor] 初始化发现 systemEvent 任务: {job.id}")

            log.info(f"[SystemEventMonitor] 已记录 {len(self._monitored_jobs)} 个 systemEvent 任务")
        except Exception as e:
            log.warning(f"[SystemEventMonitor] 初始化任务列表失败: {e}")
            import traceback
            log.warning(f"[SystemEventMonitor] Traceback: {traceback.format_exc()}")

    async def stop(self, graceful: bool = True):
        """停止监控服务"""
        self._running = False

        if self._task:
            if graceful:
                try:
                    await asyncio.wait_for(self._task, timeout=5.0)
                except asyncio.TimeoutError:
                    log.warning("[SystemEventMonitor] 优雅停止超时，强制取消")
                    self._task.cancel()
                    try:
                        await self._task
                    except asyncio.CancelledError:
                        pass
            else:
                self._task.cancel()
                try:
                    await self._task
                except asyncio.CancelledError:
                    pass

        log.info("[SystemEventMonitor] 停止监控服务")

    async def _monitor_loop(self):
        """监控主循环"""
        log.info("[SystemEventMonitor] _monitor_loop() started")
        iteration = 0
        while self._running:
            iteration += 1
            try:
                await self._check_and_replace()
            except Exception as e:
                log.exception(f"[SystemEventMonitor] 轮询异常: {e}")

            await asyncio.sleep(self.poll_interval)
        log.info("[SystemEventMonitor] _monitor_loop() stopped")

    async def _check_and_replace(self):
        """检查并替换 systemEvent 任务"""
        try:
            jobs = await asyncio.wait_for(self._cron_api.list_jobs(), timeout=10.0)
        except asyncio.TimeoutError:
            log.error("[SystemEventMonitor] 获取任务列表超时")
            return
        except Exception as e:
            log.error(f"[SystemEventMonitor] 获取任务列表失败: {e}")
            return

        systemevent_jobs = [j for j in jobs if j.payload.get('kind') == 'systemEvent']
        log.debug(f"[SystemEventMonitor] 检查 {len(jobs)} 个任务，发现 {len(systemevent_jobs)} 个 systemEvent 任务")

        for job in jobs:
            if job.payload.get('kind') != 'systemEvent':
                continue

            # 检查是否已经处理过
            if job.id in self._monitored_jobs and self._monitored_jobs[job.id].replaced:
                continue

            # 替换任务
            await self._replace_job(job)

    async def _replace_job(self, job: CronJob):
        """将 systemEvent 任务替换为 agentTurn"""
        log.info(f"[SystemEventMonitor] 准备替换任务: {job.id} ({job.name})")

        try:
            # 获取原任务配置
            command = job.payload.get('text', '')
            if not command:
                log.warning(f"[SystemEventMonitor] 任务 {job.id} 没有 text 内容，跳过")
                self._monitored_jobs[job.id] = _MonitoredJob(job_id=job.id, replaced=True)
                return

            # 构建 agentTurn payload
            new_payload = {
                "kind": "agentTurn",
                "message": command,
                "deliver": False,
                "timeout_secs": self.default_timeout_secs,
            }
            if self.default_model:
                new_payload["model"] = self.default_model

            # 构建 notify 配置
            notify = job.notify

            # 删除原任务
            log.info(f"[SystemEventMonitor] 删除原 systemEvent 任务: {job.id}")
            removed = await self._cron_api.remove_job(job.id)
            if not removed:
                log.error(f"[SystemEventMonitor] 删除任务 {job.id} 失败")
                return

            # 创建 agentTurn 任务
            create_request = CreateJobRequest(
                name=job.name,
                schedule=job.schedule,
                payload=new_payload,
                session_target="isolated",
                enabled=job.enabled,
                notify=notify,
            )

            log.info("[SystemEventMonitor] 创建 agentTurn 替换任务")
            new_job = await self._cron_api.add_job(create_request)

            # 记录已替换
            self._monitored_jobs[job.id] = _MonitoredJob(job_id=job.id, replaced=True)
            self._monitored_jobs[new_job.id] = _MonitoredJob(job_id=new_job.id, replaced=True)

            log.info(f"[SystemEventMonitor] 任务替换成功: {job.id} -> {new_job.id}")

        except Exception as e:
            log.exception(f"[SystemEventMonitor] 替换任务 {job.id} 失败: {e}")
