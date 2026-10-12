from __future__ import annotations

import asyncio
import random
import uuid
from contextlib import contextmanager
from typing import Any, Iterator

from agentclaw.community.kernel.lifecycle import LifecycleBase

from agentclaw.community.core.agentcompute.config.settings import AgentComputeConfig
from agentclaw.community.core.agentcompute.runtime.handler import SwarmRunHandler
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.metrics import MetricsPlugin

logger = get_logger(__name__)

# 12.2: join-window cap after drain grace expires; see shutdown().
_ABORT_JOIN_MAX_SECONDS = 5.0


class _NullMetrics:
    """Private no-op MetricsPlugin — slices run unmeasured when no metrics
    backend is configured (local/test profiles)."""

    def increment(self, name: str, labels: Any = None) -> None:
        return None

    def observe(self, name: str, value: float, labels: Any = None) -> None:
        return None

    def set_gauge(self, name: str, value: float, labels: Any = None) -> None:
        return None

    @contextmanager
    def time_block(self, name: str, labels: Any = None) -> Iterator[None]:
        yield


class AgentComputeWorker(LifecycleBase):

    def __init__(
        self,
        config: AgentComputeConfig,
        handler: SwarmRunHandler,
        job_repo: Any,
        metrics: MetricsPlugin | None = None,
    ) -> None:
        self._config = config
        self._handler = handler
        self._job_repo = job_repo
        self._metrics: MetricsPlugin = metrics or _NullMetrics()
        self._worker_id = uuid.uuid4().hex[:16]
        self._running = False
        self._task: asyncio.Task | None = None
        self._heartbeat_task: asyncio.Task | None = None
        self._inflight: dict[str, dict] = {}

    async def startup(self) -> None:
        if not self._config.enabled:
            logger.info("[agentcompute][worker] disabled, not starting")
            return
        self._running = True
        logger.info("[agentcompute][worker] started worker_id=%s", self._worker_id)
        self._task = asyncio.create_task(self._loop())
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

    async def shutdown(self) -> None:
        """Graceful drain (12.2, §9.3 / D34).

        State machine:

        1. stop claiming — ``_running=False``: the poll loop exits after
           its current iteration; jobs already claimed by that iteration
           still complete normally.
        2. grace wait — up to ``config.drain.grace_seconds`` for in-flight
           ``handle_slice`` calls to finish. The heartbeat loop keeps
           renewing in-flight leases while ``_inflight`` is non-empty, so a
           healthy drain does not lose claims to lease expiry. Tracked
           via ``self._inflight``.
        3. cooperative abort — grace expired with slices still in flight:
           signal ``set_slice_lost(True)`` per in-flight run so the slice
           unwinds through its checkpoint+release path (the job is
           rescheduled for reclaim, no partial writes persisted), then
           join a bounded short window.
        4. hard cancel — anything still unwinding after the abort window
           is cancelled, as before (D23).

        Deploy lead time must be >= slice budget + lease
        (``orchestration.slice_max_seconds`` + ``orchestration.lease_seconds``).
        No signal handler is installed here: the app shutdown hook calls
        this method; the intended stop-claiming signal is recorded in
        ``config.drain.stop_claiming_signal`` for the operations runbook.
        """
        logger.info("[agentcompute][worker] draining worker_id=%s", self._worker_id)
        self._running = False
        loop_task = self._task
        if loop_task is not None:
            grace = self._config.drain.grace_seconds
            done, pending = await asyncio.wait(
                {loop_task}, timeout=max(0.0, grace),
            )
            if pending:
                for run_id in list(self._inflight):
                    self._handler.set_slice_lost(True, run_id)
                abort_join = min(grace, _ABORT_JOIN_MAX_SECONDS)
                _, still_pending = await asyncio.wait(
                    {loop_task}, timeout=max(0.0, abort_join),
                )
                if still_pending:
                    logger.warning(
                        "[agentcompute][worker] drain abort timed out, cancelling %d in-flight run(s)",
                        len(self._inflight),
                    )
                    loop_task.cancel()
                    try:
                        await loop_task
                    except asyncio.CancelledError:
                        pass
            if loop_task.done():
                self._consume(loop_task)
        if self._heartbeat_task is not None:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass
        logger.info("[agentcompute][worker] stopped worker_id=%s", self._worker_id)

    @staticmethod
    def _consume(task: asyncio.Task) -> None:
        try:
            task.exception()
        except asyncio.CancelledError:
            pass

    async def run_once(self) -> int:
        await self._sample_pending_jobs()
        lease = self._config.orchestration.lease_seconds
        batch = self._config.orchestration.batch_size
        reclaimed = await asyncio.to_thread(
            self._job_repo.reclaim_batch, self._worker_id, batch * 2, lease
        )
        processed = 0
        for job in reclaimed:
            await self._run_job(job, reclaimed=True)
            processed += 1
        claimed = await asyncio.to_thread(
            self._job_repo.claim_batch, self._worker_id, None, batch, lease
        )
        for job in claimed:
            await self._run_job(job, reclaimed=False)
            processed += 1
        return processed

    async def _sample_pending_jobs(self) -> None:
        # D39 metrics are diagnostic-first: a failed sample is logged and
        # skipped, never propagated into the poll tick.
        try:
            pending = await asyncio.to_thread(self._job_repo.count_pending_jobs)
            self._metrics.set_gauge("agentcompute_pending_jobs", float(pending))
        except Exception as exc:
            logger.warning("[agentcompute][worker] pending-jobs sampling failed: %s", exc)

    async def _run_job(self, job: dict, *, reclaimed: bool) -> None:
        run_id = job["run_id"]
        tenant = job["tenant"]
        epoch = job["claim_epoch"]
        cancel = job.get("cancel_requested", False)
        if reclaimed and job.get("reclaims", 0) > self._config.orchestration.reclaim_max:
            # D40 circuit breaker: reclaim_max no-progress reclaims -> STUCK.
            self._metrics.increment("agentcompute_stuck")
            await asyncio.to_thread(
                self._handler.force_stuck,
                run_id, tenant, epoch,
                f"reclaim circuit breaker: {job['reclaims']} no-progress reclaims",
                self._worker_id,
            )
            return
        self._inflight[run_id] = job
        try:
            self._metrics.increment(
                "agentcompute_claim",
                {"kind": "reclaim" if reclaimed else "claim"},
            )
            with self._metrics.time_block(
                "agentcompute_slice", {"run": str(run_id)},
            ):
                outcome = await self._handler.handle_slice(
                    run_id, tenant, epoch, cancel, worker_id=self._worker_id,
                )
            logger.info(
                "[agentcompute][worker] run=%s outcome=%s", run_id, outcome
            )
        except Exception as exc:
            logger.exception("[agentcompute][worker] run=%s error=%s", run_id, exc)
            fenced = await asyncio.to_thread(
                self._handler.force_fail,
                run_id, tenant, epoch, str(exc), self._worker_id,
            )
            if not fenced:
                await asyncio.to_thread(
                    self._job_repo.fail,
                    run_id, tenant, str(exc), self._worker_id, epoch,
                )
        finally:
            self._inflight.pop(run_id, None)

    async def _loop(self) -> None:
        poll = self._config.orchestration.poll_interval
        jitter = self._config.orchestration.poll_jitter
        while self._running:
            try:
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("[agentcompute][worker] poll tick failed")
            delay = poll + random.uniform(-jitter, jitter)
            await asyncio.sleep(max(0.0, delay))

    async def _heartbeat_loop(self) -> None:
        # Drains keep their leases: renew while running OR while in-flight
        # slices are still unwinding (12.2), so grace-period work does not
        # get reclaimed from under a healthy drain.
        lease = self._config.orchestration.lease_seconds
        interval = lease / 3.0
        while self._running or self._inflight:
            await asyncio.sleep(interval)
            lost = []
            for run_id, job in list(self._inflight.items()):
                ok = await asyncio.to_thread(
                    self._job_repo.renew_lease,
                    run_id, job["tenant"], self._worker_id, lease, job["claim_epoch"],
                )
                if not ok:
                    logger.warning(
                        "[agentcompute][worker] lease lost for run=%s (epoch=%d)",
                        run_id, job["claim_epoch"],
                    )
                    self._handler.set_slice_lost(True, run_id)
                    lost.append(run_id)
            for run_id in lost:
                self._inflight.pop(run_id, None)
