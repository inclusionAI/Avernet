from __future__ import annotations

import asyncio
import logging
import random
import uuid
from typing import Any

from agentclaw.community.kernel.lifecycle import LifecycleBase

from agentclaw.community.core.agentcompute.config.settings import AgentComputeConfig
from agentclaw.community.core.agentcompute.runtime.swarm_run import SwarmRunHandler

logger = logging.getLogger(__name__)


class AgentComputeWorker(LifecycleBase):

    def __init__(
        self,
        config: AgentComputeConfig,
        handler: SwarmRunHandler,
        job_repo: Any,
    ) -> None:
        self._config = config
        self._handler = handler
        self._job_repo = job_repo
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
        # Graceful drain: stop claiming, let the in-flight slice persist its
        # latest completions, then unwind (§9.3 / D34 crash-clean drain).
        logger.info("[agentcompute][worker] draining worker_id=%s", self._worker_id)
        self._running = False
        self._inflight.clear()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        if self._heartbeat_task is not None:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass
        logger.info("[agentcompute][worker] stopped worker_id=%s", self._worker_id)

    async def run_once(self) -> int:
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

    async def _run_job(self, job: dict, *, reclaimed: bool) -> None:
        run_id = job["run_id"]
        tenant = job["tenant"]
        epoch = job["claim_epoch"]
        cancel = job.get("cancel_requested", False)
        if reclaimed and job.get("reclaims", 0) > self._config.orchestration.reclaim_max:
            # D40 circuit breaker: reclaim_max no-progress reclaims -> STUCK.
            await asyncio.to_thread(
                self._handler.force_stuck,
                run_id, tenant, epoch,
                f"reclaim circuit breaker: {job['reclaims']} no-progress reclaims",
                self._worker_id,
            )
            return
        self._inflight[run_id] = job
        try:
            outcome = await self._handler.handle_slice(
                run_id, tenant, epoch, cancel, worker_id=self._worker_id,
            )
            logger.info(
                "[agentcompute][worker] run=%s outcome=%s", run_id, outcome
            )
        except Exception as exc:
            logger.exception("[agentcompute][worker] run=%s error=%s", run_id, exc)
            await asyncio.to_thread(
                self._job_repo.fail, run_id, tenant, str(exc), self._worker_id, epoch
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
        lease = self._config.orchestration.lease_seconds
        interval = lease / 3.0
        while self._running:
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