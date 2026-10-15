from __future__ import annotations

import asyncio
import random
import time
import uuid
from typing import Any

from agentclaw.community.core.agentswarm.domain import models as dm
from agentclaw.community.kernel.lifecycle import LifecycleBase

from agentclaw.community.core.agentswarm.config.settings import AgentSwarmConfig
from agentclaw.community.core.agentswarm.runtime.handler import SwarmRunHandler
from agentclaw.community.core.agentswarm.runtime.offload import run_blocking
from agentclaw.community.log import get_logger

logger = get_logger(__name__)

# 12.2: join-window cap after drain grace expires; see shutdown().
_ABORT_JOIN_MAX_SECONDS = 5.0

# 4.1 sweep page size: one maintenance pass pages RUNNING runs in
# bounded chunks so a large fleet never holds one oversized session.
_SWEEP_PAGE_SIZE = 100

# 4.1/4.2: job statuses the claim/reclaim predicates can never match —
# a RUNNING run whose row sits here (or has no row at all) is a queue
# orphan: no worker will ever pick it up again without the reaper.
_DEAD_JOB_STATUSES = frozenset((
    dm.TaskQueueStatus.SUCCEEDED.value,
    dm.TaskQueueStatus.FAILED.value,
    dm.TaskQueueStatus.TIMED_OUT.value,
))


def _now_ms() -> int:
    return int(time.time() * 1000)


class AgentSwarmWorker(LifecycleBase):

    def __init__(
        self,
        config: AgentSwarmConfig,
        handler: SwarmRunHandler,
        job_repo: Any,
        run_repo: Any = None,
        guard_repo: Any = None,
    ) -> None:
        # run_repo/guard_repo host the maintenance reaper (async-run-and-
        # -resume 4.1). None keeps the pre-reaper constructor surface working
        # for embedded uses that opt out of the sweep — the DI composition
        # root always passes both, so production always sweeps.
        self._config = config
        self._handler = handler
        self._job_repo = job_repo
        self._run_repo = run_repo
        self._guard_repo = guard_repo
        self._worker_id = uuid.uuid4().hex[:16]
        self._running = False
        self._task: asyncio.Task | None = None
        self._heartbeat_task: asyncio.Task | None = None
        self._maintenance_task: asyncio.Task | None = None
        self._inflight: dict[str, dict] = {}

    def _maintenance_enabled(self) -> bool:
        return (
            self._config.maintenance.enabled
            and self._run_repo is not None
            and self._guard_repo is not None
        )

    async def startup(self) -> None:
        if not self._config.enabled:
            logger.info("[agentswarm][worker] disabled, not starting")
            return
        self._running = True
        logger.info("[agentswarm][worker] started worker_id=%s", self._worker_id)
        self._task = asyncio.create_task(self._loop())
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        if self._maintenance_enabled():
            self._maintenance_task = asyncio.create_task(self._maintenance_loop())
            logger.info(
                "[agentswarm][worker] maintenance reaper started interval=%ss worker_id=%s",
                self._config.maintenance.reaper_interval_seconds, self._worker_id,
            )

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
        logger.info("[agentswarm][worker] draining worker_id=%s", self._worker_id)
        self._running = False
        # 4.1: the reaper holds no claims and renews nothing — there is
        # nothing to drain — so it is cancelled outright, before the poll
        # ladder, and never re-enqueues work into a pod that is leaving.
        maintenance_task = self._maintenance_task
        if maintenance_task is not None:
            maintenance_task.cancel()
            try:
                await maintenance_task
            except asyncio.CancelledError:
                pass
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
                        "[agentswarm][worker] drain abort timed out, cancelling %d in-flight run(s)",
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
        logger.info("[agentswarm][worker] stopped worker_id=%s", self._worker_id)

    @staticmethod
    def _consume(task: asyncio.Task) -> None:
        try:
            task.exception()
        except asyncio.CancelledError:
            pass

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
                "[agentswarm][worker] run=%s outcome=%s", run_id, outcome
            )
        except Exception as exc:
            logger.exception("[agentswarm][worker] run=%s error=%s", run_id, exc)
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
                logger.exception("[agentswarm][worker] poll tick failed")
            delay = poll + random.uniform(-jitter, jitter)
            await asyncio.sleep(max(0.0, delay))

    async def _heartbeat_loop(self) -> None:
        # Drains keep their leases: renew while running OR while in-flight
        # slices are still unwinding (12.2), so grace-period work does not
        # get reclaimed from under a healthy drain. The poll task's liveness
        # keeps this loop alive too: during a drain _inflight is momentarily
        # empty between batch jobs while claimed work still waits in the
        # poll loop — an early exit there would let those leases expire and
        # the jobs execute twice.
        lease = self._config.orchestration.lease_seconds
        interval = lease / 3.0
        while (self._running or self._inflight
               or (self._task is not None and not self._task.done())):
            await asyncio.sleep(interval)
            lost = []
            for run_id, job in list(self._inflight.items()):
                ok = await asyncio.to_thread(
                    self._job_repo.renew_lease,
                    run_id, job["tenant"], self._worker_id, lease, job["claim_epoch"],
                )
                if not ok:
                    logger.warning(
                        "[agentswarm][worker] lease lost for run=%s (epoch=%d)",
                        run_id, job["claim_epoch"],
                    )
                    self._handler.set_slice_lost(True, run_id)
                    lost.append(run_id)
            for run_id in lost:
                self._inflight.pop(run_id, None)

    # ── 4.1/4.2: maintenance reaper (async-run-and-resume) ────────────

    async def _maintenance_loop(self) -> None:
        # Third worker task, alongside the poll loop and the heartbeat.
        # Jittered cadence with the module's existing idiom
        # (``poll + random.uniform(-jitter, jitter)``, reusing the
        # orchestration poll_jitter) so co-deployed pods decorrelate.
        interval = self._config.maintenance.reaper_interval_seconds
        jitter = self._config.orchestration.poll_jitter
        while self._running:
            try:
                acted = await self._sweep_once()
                if acted:
                    logger.info(
                        "[agentswarm][worker] reaper sweep acted on %d run(s)",
                        acted,
                    )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("[agentswarm][worker] reaper sweep failed")
            delay = interval + random.uniform(-jitter, jitter)
            await asyncio.sleep(max(0.0, delay))

    async def _sweep_once(self) -> int:
        page = 1
        acted = 0
        while True:
            runs = await run_blocking(
                self._run_repo.list_status_page,
                dm.RunStatus.RUNNING, page, _SWEEP_PAGE_SIZE,
            )
            for run in runs.items:
                acted += await self._reap_or_ignore(run)
            if page * runs.page_size >= runs.total:
                return acted
            page += 1

    def _fresh_run(self, run: dm.Run) -> bool:
        """One lease window of submit grace: run.create commits before the
        job enqueue does, so a run inside this window may simply not have
        its job row yet — re-enqueueing against it would race the
        submitter's own enqueue (whose failure compensates by failing the
        run). The grace is also the minimum time a healthy fleet needs to
        surface any job row at all.
        """
        if not run.started_at:
            return False
        elapsed_ms = _now_ms() - int(run.started_at)
        return elapsed_ms < self._config.orchestration.lease_seconds * 1000.0

    def _wall_clock_expired(self, run: dm.Run) -> bool:
        """4.2 deadline source: the run's wall budget runs from
        ``run.started_at`` with the SAME budget the slice's
        TerminationEvaluator enforces
        (``orchestration.wall_clock_seconds``), so the reaper's forced
        TIMED_OUT and a rehydrated slice's own termination can never
        disagree. The job row's ``deadline_at`` is deliberately NOT
        consulted: it mirrors the submit-sync wait budget
        (``max(slice_max_seconds, 1)`` computed at enqueue), not the
        run's wall clock — using it would kill healthy long runs
        because today it defaults to one slice budget from enqueue.
        """
        if not run.started_at:
            return False
        elapsed_ms = _now_ms() - int(run.started_at)
        return elapsed_ms >= self._config.orchestration.wall_clock_seconds * 1000.0

    async def _reap_or_ignore(self, run: dm.Run) -> int:
        try:
            if self._fresh_run(run):
                return 0
            job = await run_blocking(
                self._job_repo.get_run_job, run.run_id, run.tenant,
            )
            if job is not None and job["status"] not in _DEAD_JOB_STATUSES:
                # Claimable, interpreted exactly like the claim
                # predicates: PENDING claims once run_at is due; RUNNING
                # is held by a live owner or reclaimable on lease expiry
                # (the D40 breaker owns crash loops). The queue will act
                # without the reaper — stay silent.
                return 0
            if self._wall_clock_expired(run):
                return await self._expire_deadline_orphan(run, job)
            return await self._requeue_orphan(run)
        except asyncio.CancelledError:
            raise
        except Exception:
            # Retention-sweeper idiom: one bad run never wedges the sweep.
            logger.exception(
                "[agentswarm][worker] reaper probe failed for run=%s", run.run_id,
            )
            return 0

    async def _requeue_orphan(self, run: dm.Run) -> int:
        acted = await run_blocking(
            self._job_repo.requeue_orphan, run.run_id, run.tenant,
            self._submit_deadline_seconds(),
        )
        if acted:
            await self._emit_orphan_guard_event(run)
            logger.info(
                "[agentswarm][worker] reaper re-enqueued queue orphan run=%s",
                run.run_id,
            )
        return 1 if acted else 0

    async def _expire_deadline_orphan(self, run: dm.Run, job: dict | None) -> int:
        """4.2 deadline leg: past-wall-clock orphans go terminal TIMED_OUT
        through the fenced ``update_status`` path (the same write
        force_stuck/force_fail use), freeing the tenant admission slot
        and the active idempotency key via the terminal-status handling.
        A no-row orphan gets the guarded requeue FIRST: the D28 fence
        only matches a job row at the caller's epoch, so the reaper
        provisions its own fence. A claiming worker racing us bumps the
        epoch and our write matches nothing — the claimant's slice
        fires and owns the same TIMED_OUT through 4.4's
        past-deadline-still-claimable path. Bot teardown and message
        retention stay with the slice's terminal paths: the orphaned
        job row is (re)marked terminal by a claim's already-terminal
        fast path, never re-runs.
        """
        if job is None:
            await run_blocking(
                self._job_repo.requeue_orphan, run.run_id, run.tenant,
                self._submit_deadline_seconds(),
            )
            job = await run_blocking(
                self._job_repo.get_run_job, run.run_id, run.tenant,
            )
            if job is None:
                return 0
        ok = await run_blocking(
            self._run_repo.update_status,
            run.run_id, run.tenant, dm.RunStatus.TIMED_OUT,
            termination_reason=dm.TerminationReason.WALL_CLOCK,
            error="orphan reaper: wall-clock deadline expired",
            claim_epoch=job["claim_epoch"],
        )
        if not ok:
            logger.info(
                "[agentswarm][worker] reaper age-off fenced out run=%s (a "
                "newer claim owns the outcome)",
                run.run_id,
            )
            return 0
        await self._emit_orphan_guard_event(run)
        logger.info(
            "[agentswarm][worker] reaper aged off past-deadline orphan run=%s",
            run.run_id,
        )
        return 1

    def _submit_deadline_seconds(self) -> float:
        # The service's own enqueue semantics (service/impl.py):
        # max(slice_max_seconds, 1) — mirrored so a re-enqueued orphan
        # carries the same deadline_at contract as a fresh submit.
        slice_max = self._config.orchestration.slice_max_seconds
        return max(float(slice_max or 3600.0), 1.0)

    async def _emit_orphan_guard_event(self, run: dm.Run) -> None:
        await run_blocking(
            self._guard_repo.create,
            dm.GuardEvent(
                id=dm.EventId(uuid.uuid4().hex),
                run_id=run.run_id,
                tenant=run.tenant,
                agent_id=None,
                kind=dm.GuardKind.STUCK,
                details={"reason": "orphan"},
                created_at=dm.EpochMillis(_now_ms()),
            ),
        )
