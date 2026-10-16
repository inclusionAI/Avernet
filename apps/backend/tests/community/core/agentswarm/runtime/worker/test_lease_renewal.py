"""Lease-renewal decoupling tests (root-cause fix): renewal runs on a
dedicated wall-clock thread, so a CPU-bound slice starving the asyncio loop
cannot lapse the lease and hand the job to a peer's reclaim (the
``slice_lost`` failure). Mirrors the drain-harness fakes in
``test_drain_breaker.py``.
"""
from __future__ import annotations

import asyncio
import threading
import time

import pytest

from agentclaw.community.core.agentswarm.config.settings import (
    AgentSwarmConfig, AgentSwarmDrainConfig, AgentSwarmOrchestrationConfig,
)
from agentclaw.community.core.agentswarm.runtime.worker import AgentSwarmWorker

from tests.community.core.agentswarm.runtime.worker.test_drain_breaker import (
    DrainHandler, DrainJobRepo, TENANT, _drain_config, _stub_job,
)


def _lease_config(lease: int) -> AgentSwarmConfig:
    orch = AgentSwarmOrchestrationConfig(
        poll_interval=10.0, poll_jitter=0.0, lease_seconds=lease,
        batch_size=4, slice_max_seconds=10.0,
    )
    return AgentSwarmConfig(
        orchestration=orch,
        drain=AgentSwarmDrainConfig(grace_seconds=30.0),
    )


class _RenewingRepo(DrainJobRepo):
    """Records renewals; returns a scripted verdict per run_id."""

    def __init__(self) -> None:
        super().__init__()
        self.verdicts: dict[str, bool] = {}
        self.renewed = threading.Event()

    def renew_lease(self, run_id, tenant, worker_id, lease_seconds, claim_epoch):
        self.leases.append((run_id, claim_epoch))
        self.renewed.set()
        return self.verdicts.get(run_id, True)


class _FastRenewalWorker(AgentSwarmWorker):
    """Test seam: shrink the renewal interval so ticks are observable fast."""

    def __init__(self, *args, interval: float = 0.05, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._test_interval = interval

    def _lease_interval(self, lease: int) -> float:
        return self._test_interval


class TestLeaseRenewalThread:

    def test_inflight_run_is_renewed_and_non_inflight_is_not(self):
        repo = _RenewingRepo()
        repo.enqueue_job(_stub_job(run_id="leased-run", epoch=1))
        worker = _FastRenewalWorker(
            config=_lease_config(lease=30), handler=DrainHandler(), job_repo=repo,
        )
        try:
            repo.renewed.clear()
            worker._lease_thread = threading.Thread(
                target=worker._lease_renewal_loop, daemon=True,
            )
            worker._lease_stop.clear()
            worker._running = True
            worker._mark_inflight("leased-run", _stub_job(run_id="leased-run"))
            worker._lease_thread.start()
            assert repo.renewed.wait(timeout=15.0)
        finally:
            worker._lease_stop.set()
            worker._lease_thread.join(timeout=5.0)

        renewed_ids = {rid for rid, _ in repo.leases}
        assert "leased-run" in renewed_ids
        assert renewed_ids == {"leased-run"}

    def test_renew_false_signals_slice_lost_and_removes_from_inflight(self):
        async def main():
            repo = _RenewingRepo()
            repo.verdicts["fenced-run"] = False
            handler = DrainHandler()
            worker = _FastRenewalWorker(
                config=_lease_config(lease=30), handler=handler, job_repo=repo,
            )
            worker._loop_ref = asyncio.get_running_loop()
            worker._lease_stop.clear()
            worker._running = True
            worker._mark_inflight("fenced-run", _stub_job(run_id="fenced-run"))
            worker._lease_thread = threading.Thread(
                target=worker._lease_renewal_loop, daemon=True,
            )
            worker._lease_thread.start()
            try:
                deadline = time.monotonic() + 15.0
                while time.monotonic() < deadline:
                    if ("set-slice-lost", True, "fenced-run") in handler.calls:
                        break
                    await asyncio.sleep(0.05)
            finally:
                worker._lease_stop.set()
                worker._lease_thread.join(timeout=5.0)
            return handler, worker

        handler, worker = asyncio.run(main())

        assert ("set-slice-lost", True, "fenced-run") in handler.calls
        assert "fenced-run" not in worker._inflight_snapshot()

    def test_renewal_continues_while_event_loop_blocked(self):
        # No job enqueued: the poll loop claims nothing and never parks in
        # handle_slice, leaving the lease thread as the only moving part.
        repo = _RenewingRepo()
        handler = DrainHandler()
        worker = _FastRenewalWorker(
            config=_lease_config(lease=30), handler=handler, job_repo=repo,
            interval=0.1,
        )

        async def run():
            await worker.startup()
            worker._mark_inflight("blocked-run", _stub_job(run_id="blocked-run"))
            await asyncio.to_thread(repo.renewed.wait, 15.0)
            repo.leases.clear()
            return worker

        running_loop = asyncio.new_event_loop()
        try:
            running_loop.run_until_complete(run())
            # time.sleep blocks the loop's thread; asyncio.sleep would yield.
            # A tick here proves renewal schedules on wall-clock, not the loop.
            time.sleep(1.0)
            renewed_during_block = [rid for rid, _ in repo.leases]
        finally:
            worker._running = False
            worker._clear_inflight("blocked-run")
            running_loop.run_until_complete(worker.shutdown())
            running_loop.close()

        assert "blocked-run" in renewed_during_block


class TestLeaseThreadLifecycle:

    def test_startup_starts_and_shutdown_stops_lease_thread(self):
        async def main():
            repo = _RenewingRepo()
            worker = _FastRenewalWorker(
                config=_lease_config(lease=30), handler=DrainHandler(),
                job_repo=repo,
            )
            await worker.startup()
            started = worker._lease_thread is not None and worker._lease_thread.is_alive()
            await worker.shutdown()
            stopped = worker._lease_thread is None
            return started, stopped

        started, stopped = asyncio.run(main())
        assert started is True
        assert stopped is True


class TestLeaseFloorConfig:

    def test_low_lease_seconds_rejected(self):
        with pytest.raises(ValueError, match="lease_seconds"):
            AgentSwarmOrchestrationConfig.from_block({"lease_seconds": 10})

    def test_normal_lease_seconds_passes(self):
        cfg = AgentSwarmOrchestrationConfig.from_block({"lease_seconds": 60})
        assert cfg.lease_seconds == 60

    def test_default_lease_seconds_passes(self):
        cfg = AgentSwarmOrchestrationConfig.from_block({})
        assert cfg.lease_seconds >= 30