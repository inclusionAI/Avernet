"""容器宿主（06-evolution-run.zh-CN §10）：在一个一次性沙箱里跑策略 worker。

与 `InProcessHost` 对外的语义一致（`run_once(worker_id) -> bool`），区别只在执行
边界：策略不在 backend 进程里运行，而是在按 digest 固定镜像的沙箱中，由容器内
worker 通过作业协议自行认领、心跳与完成作业。本类只做沙箱生命周期与超时兜底。
"""

from __future__ import annotations

import asyncio
import secrets
import time
from dataclasses import dataclass, field
from typing import Callable

from .sandbox import JobWorkerSandbox, job_worker_spec

__all__ = ["ContainerHost"]


def _default_command(spec) -> str:
    return (
        "python -m agentclaw.community.plugins.evolution.entrypoint "
        f"--worker {spec.worker_id}"
    )


@dataclass
class ContainerHost:
    """作业宿主：每个作业一个沙箱，结束（或被超时取消）后立即销毁。"""

    sandbox: JobWorkerSandbox
    store: object
    jobs: object
    runs: object
    image_of: Callable[[object], str]
    job_endpoint: str
    strategy_refs: tuple[str, ...] = ()
    tokens: object = None
    command: Callable[[object], str] = _default_command
    poll_interval_s: float = 0.05
    settle_s: float = 5.0
    worker_id_factory: Callable[[], str] = field(
        default_factory=lambda: lambda: "wk_" + secrets.token_hex(6)
    )
    last_error: str | None = None

    async def run_once(self, worker_id: str) -> bool:
        del worker_id  # 认领发生在沙箱内：worker id 属于沙箱，不属于宿主进程
        if not self.store.has_queued_job(list(self.strategy_refs)):
            return False
        await self._execute()
        return True

    async def run_pending(self, worker_id: str, *, max_jobs: int = 100) -> int:
        count = 0
        while count < max_jobs and await self.run_once(worker_id):
            count += 1
        return count

    async def _execute(self) -> None:
        worker_id = self.worker_id_factory()
        token = self.tokens.issue(worker_id) if self.tokens is not None else ""
        try:
            spec = job_worker_spec(
                worker_id=worker_id,
                image=self.image_of(None),
                job_endpoint=self.job_endpoint,
                job_token=token,
            )
        except Exception as exc:  # noqa: BLE001 - 规格不合法是配置错误
            self.last_error = f"{type(exc).__name__}: {exc}"
            return

        try:
            sandbox_id = await self.sandbox.create(spec)
        except Exception as exc:  # noqa: BLE001 - 创建失败时不留下任何东西
            self.last_error = f"{type(exc).__name__}: {exc}"
            return

        try:
            code = await self.sandbox.exec(
                sandbox_id, self.command(spec), timeout_ms=self._timeout_ms()
            )
            if code != 0:
                self.last_error = f"worker exited with {code}"
            await self._settle(worker_id)
        except Exception as exc:  # noqa: BLE001 - worker 异常不影响销毁
            self.last_error = f"{type(exc).__name__}: {exc}"
        finally:
            try:
                await self.sandbox.destroy(sandbox_id)
            except Exception as exc:  # noqa: BLE001 - 销毁失败不掩盖运行结果
                self.last_error = f"destroy failed: {exc}"

    async def _settle(self, worker_id: str) -> None:
        """exec 返回后确认 worker 没有留下未关闭的作业。

        有界等待（`settle_s`）：仅在 worker 尚未落完最后一批写入时需要观察窗口。
        期间作业仍开放即视为 worker 异常退出：按 §7.2 把运行作为下一次尝试重新
        排队（没有剩余尝试时以 `max_attempts` 失败），沙箱随后销毁。平台不把这种
        情况当成取消——策略提交过的候选与运行记录都保留，重新派发后策略按
        `attempt + 1` 继续。
        """

        deadline = time.monotonic() + self.settle_s
        while time.monotonic() < deadline:
            if self.store.open_job_for_worker(worker_id) is None:
                return
            await asyncio.sleep(self.poll_interval_s)
        open_job = self.store.open_job_for_worker(worker_id)
        if open_job is None:
            return
        self.last_error = "worker_exited_with_open_job"
        job, _run = open_job
        await self.jobs.requeue(job.id)

    def _ttl_minutes(self) -> int:
        from .sandbox import DEFAULT_TTL_MINUTES

        return DEFAULT_TTL_MINUTES

    def _timeout_ms(self) -> int:
        return int(self._ttl_minutes() * 60 * 1000)
