"""执行循环（06-evolution-run.zh-CN §10）：把队列消费接进进程生命周期。

`TriggerScheduler` 只会在被调用时把 cron 绑定变成队列里的运行，
`ContainerHost` 只会在被调用时把一个作业派发进沙箱。两者都不会自己跑：
`EvolutionRunner` 就是那个驱动者，作为 `Lifecycle` 参与者在 startup 时
启动一个后台任务、在 shutdown 时取消它。

分工固定为「先调度、再派发」：

1. ``scheduler.tick()`` —— 把本分钟该触发的 cron 绑定变成 queued 运行
2. ``host.run_once()`` —— 每次派发一个作业到全新的一次性沙箱

队列排空后按 ``poll_interval_s`` 等待，等待可被 shutdown 立即打断，
因此停机不需要等一个完整的轮询周期。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from agentclaw.community.kernel.lifecycle import LifecycleBase

__all__ = ["EvolutionRunner"]


@dataclass
class EvolutionRunner(LifecycleBase):
    """按间隔驱动「调度 + 派发」的后台循环；一个进程一个。

    默认不启动后台消费，避免给已有部署带来后台循环副作用。只有
    设置 ``enabled=True``（生产通过环境变量 ``EVOLUTION_CONSUMER_ENABLED=1``
    开启）时，``startup()`` 才会驱动 ``scheduler.tick()`` + ``host.run_once()``；
    关闭时两者均为无操作，平台仍可通过手动调用 ``tick()`` 或直接驱动
    ``host.run_once()`` 消费队列。
    """

    host: Any
    scheduler: Any
    jobs: Any = None
    enabled: bool = False
    poll_interval_s: float = 15.0
    max_jobs_per_tick: int = 1
    worker_id: str = "evolution-runner"
    last_error: str | None = None
    dispatched: int = 0
    _task: asyncio.Task | None = field(default=None, repr=False)
    _stop: asyncio.Event = field(default_factory=asyncio.Event, repr=False)

    async def startup(self) -> None:
        """起后台循环；重复 startup 不会起第二个任务。

        禁用时为无操作，不创建任务，不影响已有部署。
        """

        if not self.enabled:
            return
        if self._task is not None and not self._task.done():
            return
        self._stop = asyncio.Event()
        self._task = asyncio.create_task(self._loop(), name="evolution-runner")

    async def shutdown(self) -> None:
        """取消循环并等它退出；等待中的轮询会被立即打断。

        禁用时为无操作。
        """

        if not self.enabled:
            return
        self._stop.set()
        task, self._task = self._task, None
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def tick(self, now: datetime | None = None) -> int:
        """一次「回收过期租约 + 调度 + 派发」，返回本次派发进沙箱的作业数。"""

        # 先回收：崩溃 worker 的租约到期后，运行回到 queued 并在同一轮被重新派发
        # （§7.2）。`jobs` 未注入时（禁用 / 测试替身）跳过回收。
        if self.jobs is not None:
            await self.jobs.expire_leases()
        await self.scheduler.tick(now or datetime.now(timezone.utc))
        dispatched = 0
        while dispatched < self.max_jobs_per_tick:
            if not await self.host.run_once(self.worker_id):
                break
            dispatched += 1
        self.dispatched += dispatched
        return dispatched

    async def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - 一次失败不能让消费循环停摆
                self.last_error = f"{type(exc).__name__}: {exc}"
            if await self._sleep_until_next_tick():
                return

    async def _sleep_until_next_tick(self) -> bool:
        """等到下一个轮询点；返回 True 表示循环已被要求停止。"""

        if self._stop.is_set():
            return True
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=self.poll_interval_s)
        except TimeoutError:
            return False
        return True
