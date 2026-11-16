"""执行循环（06 §10）：调度 → 派发 → 轮询，与 shutdown 安全交互。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import pytest

from agentclaw.community.core.evolution_run import EvolutionRunner


@dataclass
class FakeHost:
    """镜像 `ContainerHost.run_once` 的语义：返回 True 才算真的派发了一次。"""

    outcomes: list[bool]
    calls: int = 0
    dispatched: int = 0
    raised: type[Exception] | None = None

    async def run_once(self, worker_id: str) -> bool:
        if self.raised is not None:
            exc = self.raised
            self.raised = None
            raise exc
        self.calls += 1
        outcome = self.outcomes.pop(0) if self.outcomes else False
        if outcome:
            self.dispatched += 1
        return outcome


@dataclass
class FakeScheduler:
    ticks: int = 0
    raised: type[Exception] | None = None

    async def tick(self, now=None) -> None:
        self.ticks += 1
        if self.raised is not None:
            exc = self.raised
            self.raised = None
            raise exc


@dataclass
class FakeJobs:
    sweeps: int = 0

    async def expire_leases(self, now=None) -> list[str]:
        self.sweeps += 1
        return []


@pytest.mark.asyncio
async def test_tick_sweeps_expired_leases_before_scheduling() -> None:
    """§7.2：崩溃 worker 的租约必须在每轮调度前回收，否则运行永远卡在 running。"""

    host = FakeHost([False])
    scheduler = FakeScheduler()
    jobs = FakeJobs()
    runner = EvolutionRunner(host=host, scheduler=scheduler, jobs=jobs)

    await runner.tick()
    assert jobs.sweeps == 1
    assert scheduler.ticks == 1


@pytest.mark.asyncio
async def test_tick_skips_the_sweep_when_no_job_service_is_wired() -> None:
    host = FakeHost([False])
    scheduler = FakeScheduler()
    runner = EvolutionRunner(host=host, scheduler=scheduler)

    dispatched = await runner.tick()
    assert dispatched == 0
    assert scheduler.ticks == 1


@pytest.mark.asyncio
async def test_tick_dispatches_until_queue_drains() -> None:
    host = FakeHost([True, True, False])
    scheduler = FakeScheduler()
    runner = EvolutionRunner(host=host, scheduler=scheduler, max_jobs_per_tick=4)

    dispatched = await runner.tick()
    assert dispatched == 2
    assert host.calls == 3      # 两次派发 + 一次排空探测
    assert host.dispatched == 2
    assert scheduler.ticks == 1


@pytest.mark.asyncio
async def test_tick_stops_at_max_jobs_per_tick() -> None:
    host = FakeHost([True, True, True])
    scheduler = FakeScheduler()
    runner = EvolutionRunner(host=host, scheduler=scheduler, max_jobs_per_tick=2)

    dispatched = await runner.tick()
    assert dispatched == 2
    assert host.dispatched == 2


@pytest.mark.asyncio
async def test_loop_runs_ticks_at_the_interval_and_survives_a_failing_tick() -> None:
    host = FakeHost([False])
    scheduler = FakeScheduler(raised=RuntimeError("boom"))
    runner = EvolutionRunner(
        host=host,
        scheduler=scheduler,
        enabled=True,
        poll_interval_s=0.01,
        max_jobs_per_tick=1,
    )

    await runner.startup()
    await asyncio.sleep(0.06)  # ~6 ticks despite one failing
    await runner.shutdown()

    assert scheduler.ticks > 3
    assert "boom" in runner.last_error


@pytest.mark.asyncio
async def test_shutdown_stops_the_loop_immediately() -> None:
    host = FakeHost([False])
    scheduler = FakeScheduler()
    runner = EvolutionRunner(
        host=host,
        scheduler=scheduler,
        enabled=True,
        poll_interval_s=3600,
        max_jobs_per_tick=1,
    )

    await runner.startup()
    await asyncio.sleep(0.01)
    ticks_before = scheduler.ticks
    await runner.shutdown()
    await asyncio.sleep(0.05)
    assert scheduler.ticks == ticks_before
    assert runner.last_error is None


@pytest.mark.asyncio
async def test_startup_is_idempotent() -> None:
    host = FakeHost([False])
    scheduler = FakeScheduler()
    runner = EvolutionRunner(
        host=host,
        scheduler=scheduler,
        enabled=True,
        poll_interval_s=3600,
        max_jobs_per_tick=1,
    )

    await runner.startup()
    task = runner._task
    await runner.startup()
    assert runner._task is task
    await runner.shutdown()
    assert runner._task is None


@pytest.mark.asyncio
async def test_runner_disabled_does_not_start_task() -> None:
    host = FakeHost([False])
    scheduler = FakeScheduler()
    runner = EvolutionRunner(host=host, scheduler=scheduler)

    assert not runner.enabled
    await runner.startup()
    assert runner._task is None
    await runner.shutdown()  # no-op when disabled
    assert scheduler.ticks == 0
    assert runner.last_error is None
