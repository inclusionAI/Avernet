"""进程内宿主（06-evolution-run.zh-CN §9）：认领租约作业、构建上下文、执行 run(ctx)。"""

from __future__ import annotations

import asyncio
from dataclasses import asdict, is_dataclass
from typing import Any, Callable

from agentclaw.community.core.evolution_strategy import (
    BudgetExhausted as SdkBudgetExhausted,
)

from .budget import BudgetUsage
from .errors import BudgetExhausted
from .records import ClaimedJob
from .store import EvolutionStore

__all__ = ["InProcessHost"]


class InProcessHost:
    """把注册为进程内的策略跑在一个作业上；策略是 `async run(ctx)` 的可调用对象。"""

    def __init__(
        self,
        *,
        strategies: dict[str, Callable[[], Any]],
        store: EvolutionStore,
        jobs,
        operations,
        budget,
        genome,
        ctx_factory,
        promotion=None,
        ledger=None,
        verifier=None,
        models_provider=None,
        experience_provider=None,
        agents_provider=None,
        evaluation_provider=None,
        heartbeat_s: float = 20.0,
    ) -> None:
        self._strategies = strategies
        self._store = store
        self._jobs = jobs
        self._operations = operations
        self._budget = budget
        self._genome = genome
        self._ctx_factory = ctx_factory
        self._promotion = promotion
        self._ledger = ledger
        self._verifier = verifier
        self._models = models_provider
        self._experience = experience_provider
        self._agents_provider = agents_provider
        self._evaluation_provider = evaluation_provider
        self._heartbeat_s = heartbeat_s
        self.last_error: str | None = None

    async def run_once(self, worker_id: str) -> bool:
        """认领并执行一个作业；没有可认领的返回 False。"""

        claimed: ClaimedJob | None = await self._jobs.claim(
            worker_id, list(self._strategies)
        )
        if claimed is None:
            return False
        await self._execute(claimed)
        return True

    async def run_pending(self, worker_id: str, *, max_jobs: int = 100) -> int:
        count = 0
        while count < max_jobs and await self.run_once(worker_id):
            count += 1
        return count

    async def _execute(self, claimed: ClaimedJob) -> None:
        job, run = claimed.job, claimed.run
        strategy = self._strategies[self._strategy_key(run)]
        if isinstance(strategy, type):
            strategy = strategy()
        ctx = await self._ctx_factory(
            run,
            models_provider=self._models,
            experience_provider=self._experience,
            agents_provider=self._agents_provider,
            evaluation_provider=self._evaluation_provider,
            promotion=self._promotion,
            ledger=self._ledger,
            verifier=self._verifier,
        )

        stop = asyncio.Event()

        async def heartbeat() -> None:
            while not stop.is_set():
                await asyncio.sleep(self._heartbeat_s)
                if stop.is_set():
                    return
                try:
                    state = await self._jobs.heartbeat(job.id, job.fencing_token)
                except Exception:  # noqa: BLE001 - 失去租约时停止续期
                    return
                if state.cancelled:
                    return

        beat = asyncio.create_task(heartbeat())
        try:
            summary = await strategy.run(ctx)
            await self._jobs.complete(job.id, job.fencing_token, _summary_dict(summary))
        except (BudgetExhausted, SdkBudgetExhausted) as exc:
            # BudgetService 已把运行结束为 budget_exhausted；这里只关闭作业。
            await self._close_job(job, str(exc))
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - 策略抛出的任何异常都是可重试失败
            self.last_error = f"{type(exc).__name__}: {exc}"
            retryable = job.attempt < 3
            await self._jobs.fail(
                job.id, job.fencing_token, reason=str(exc), retryable=retryable
            )
        finally:
            stop.set()
            beat.cancel()
            try:
                await beat
            except (asyncio.CancelledError, Exception):
                pass
            await self._operations.cancel_unfinished(run.id)

    async def _close_job(self, job, reason: str) -> None:
        try:
            await self._jobs.fail(
                job.id, job.fencing_token, reason=reason, retryable=False
            )
        except Exception:  # noqa: BLE001
            pass

    def _strategy_key(self, run) -> str:
        return f"{run.strategy}@{run.strategy_version}"


def _summary_dict(summary: Any) -> dict[str, Any]:
    if is_dataclass(summary):
        return asdict(summary)
    if isinstance(summary, dict):
        return summary
    return {"summary": str(summary)}


def _noop_charge() -> BudgetUsage:
    return BudgetUsage.of()
