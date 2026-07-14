"""TaskWorker — the in-process loop that drains the queue (one per pod).

A ``Lifecycle`` singleton: ``startup()`` launches an asyncio loop,
``shutdown()`` cancels it (same shape as ``DesktopBotLifecycle``). The loop:

1. claims a small batch of due tasks (``repo.claim_batch`` — the DB-level
   single-winner CAS), off the event loop via ``asyncio.to_thread``;
2. runs each claimed task's handler concurrently, bounded by a semaphore;
3. maps the handler's :class:`TaskOutcome` back to a repository transition;
4. **greedy re-poll** when the batch came back full (a backlog likely exists)
   else sleeps a jittered idle interval.

Give-up is a deadline from first enqueue, enforced entirely DB-side by the
repository: a past-deadline task is retired ``TIMED_OUT`` at claim time (never
handed to a handler), and a reschedule/retry whose new ``run_at`` would
overshoot the deadline is timed out instead. So the worker itself does no
timing — it passes durations, never timestamps. A handler that raises is
treated as an implicit :class:`Retry` with exponential backoff.

All DB and handler calls run via ``asyncio.to_thread`` so the synchronous ORM
never blocks FastAPI's event loop. Handler bodies run concurrently (bounded by
``max_concurrency``); the worker's own queue statements (claim + outcome
writes) are serialized behind a per-worker lock so a single shared SQLite
connection (local/test) stays safe without sacrificing handler parallelism.
"""
from __future__ import annotations

import asyncio
import os
import random
import socket
import uuid
from typing import Optional

from injector import inject

from agentclaw.community.core.task_queue.repository.protocol import TaskQueueRepositoryProtocol
from agentclaw.community.core.task_queue.services.registry import HandlerRegistry
from agentclaw.community.core.task_queue.types import (
    Complete,
    Fail,
    Reschedule,
    Retry,
    TaskRecord,
)
from agentclaw.community.di.config import TaskQueueWorkerConfig
from agentclaw.community.kernel.lifecycle import LifecycleBase
from agentclaw.community.log import get_logger
from agentclaw.community.utils.env_utils import get_current_env

logger = get_logger()


def _make_worker_id() -> str:
    """A per-process identity: host:pid:rand. Stored in ``claimed_by`` so a
    claim is traceable and the holder-guard CAS is unambiguous."""
    return f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"


class TaskWorker(LifecycleBase):
    """Claims and runs queued tasks for this process."""

    @inject
    def __init__(
        self,
        repo: TaskQueueRepositoryProtocol,
        registry: HandlerRegistry,
        config: TaskQueueWorkerConfig,
    ) -> None:
        self._repo = repo
        self._registry = registry
        self._config = config
        self._worker_id = _make_worker_id()
        self._env = get_current_env()
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._sem = asyncio.Semaphore(max(1, config.max_concurrency))
        # Serializes this worker's OWN queue DB statements. Handler bodies run
        # concurrently (the valuable I/O parallelism), but the brief claim /
        # outcome writes go one at a time. This keeps the worker safe against a
        # single shared SQLite connection (local/test StaticPool) without
        # giving up handler concurrency; on a prod connection pool it costs
        # only negligible serialization of fast writes.
        self._db_lock = asyncio.Lock()

    # ── lifecycle ───────────────────────────────────────────────────────
    async def startup(self) -> None:
        if not self._config.enabled:
            logger.info(
                "[TaskWorker] disabled (task_queue_worker.enabled=false) — not starting"
            )
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info(
            "[TaskWorker] started worker_id=%s env=%s batch=%d poll=%.1fs lease=%ds",
            self._worker_id,
            self._env,
            self._config.batch_size,
            self._config.poll_interval_seconds,
            self._config.lease_seconds,
        )

    async def shutdown(self) -> None:
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("[TaskWorker] stopped worker_id=%s", self._worker_id)

    # ── loop ────────────────────────────────────────────────────────────
    async def _loop(self) -> None:
        while self._running:
            try:
                claimed = await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception:  # never let one tick kill the loop
                logger.exception("[TaskWorker] poll tick failed")
                claimed = 0
            # Greedy: a full batch implies more is waiting → re-poll now.
            if claimed >= self._config.batch_size:
                continue
            await asyncio.sleep(self._idle_delay())

    def _idle_delay(self) -> float:
        jitter = self._config.poll_jitter_seconds
        delta = random.uniform(-jitter, jitter) if jitter else 0.0
        return max(0.0, self._config.poll_interval_seconds + delta)

    async def _db(self, fn, /, **kwargs):
        """Run a synchronous repository call off the event loop, serialized
        against this worker's other DB statements (see ``_db_lock``)."""
        async with self._db_lock:
            return await asyncio.to_thread(fn, **kwargs)

    async def _write_outcome(self, fn, /, *, task_id, **kwargs) -> bool:
        """Apply a holder-guarded outcome transition (complete/reschedule/fail)
        and surface a lost-lease no-op.

        A ``False`` return means the CAS predicate didn't match — this worker
        no longer holds the task (its lease expired and another worker took it
        over). That is safe (the new holder owns the outcome), but we log it so
        a handler routinely outliving its lease is visible in monitoring rather
        than a silent dropped write.
        """
        ok = await self._db(fn, task_id=task_id, **kwargs)
        if ok is False:
            logger.warning(
                "[TaskWorker] outcome write for task id=%s was a no-op — lease "
                "lost to another worker (handler outran lease_seconds=%ds?)",
                task_id,
                self._config.lease_seconds,
            )
        return ok

    async def run_once(self) -> int:
        """Claim one batch and run it. Returns the number of tasks claimed.

        Public so tests can drive the worker deterministically without sleeping.
        """
        claimed = await self._db(
            self._repo.claim_batch,
            worker_id=self._worker_id,
            env=self._env,
            limit=self._config.batch_size,
            lease_seconds=self._config.lease_seconds,
        )
        if not claimed:
            return 0
        await asyncio.gather(*(self._run_guarded(task) for task in claimed))
        return len(claimed)

    async def _run_guarded(self, task: TaskRecord) -> None:
        async with self._sem:
            try:
                await self._run_one(task)
            except asyncio.CancelledError:
                raise
            except Exception:
                # Defensive: _run_one already maps handler errors to Retry; this
                # only catches a failure in the mapping/DB write itself. Leave
                # the row RUNNING — its lease will expire and another worker
                # reclaims it.
                logger.exception(
                    "[TaskWorker] task id=%s type=%s outcome handling failed",
                    task.id,
                    task.task_type,
                )

    # ── per-task execution ──────────────────────────────────────────────
    async def _run_one(self, task: TaskRecord) -> None:
        # No deadline check here — the repository times out past-deadline tasks
        # at claim time, so a task we receive is always still within deadline.
        handler = self._registry.get(task.task_type)
        if handler is None:
            logger.error(
                "[TaskWorker] no handler registered for task_type=%s (id=%s) — failing",
                task.task_type,
                task.id,
            )
            await self._write_outcome(
                self._repo.fail,
                task_id=task.id,
                worker_id=self._worker_id,
                error=f"no handler registered for task_type={task.task_type}",
            )
            return

        # Keep the claim alive while the handler runs: a long handler (e.g. a
        # slow build) would otherwise outlive lease_seconds and be re-claimed +
        # double-run on another pod. The heartbeat renews the lease DB-side until
        # the handler returns (or the lease is lost to another worker).
        heartbeat = asyncio.create_task(self._heartbeat(task.id))
        try:
            outcome = await asyncio.to_thread(handler.handle, task.payload)
        except Exception as exc:  # handler raising == implicit Retry with backoff
            logger.warning(
                "[TaskWorker] handler raised for task id=%s type=%s: %r",
                task.id,
                task.task_type,
                exc,
            )
            outcome = Retry(error=repr(exc))
        finally:
            heartbeat.cancel()
            try:
                await heartbeat
            except asyncio.CancelledError:
                pass

        await self._apply(task, outcome)

    async def _heartbeat(self, task_id: int) -> None:
        """Periodically extend the running task's lease until cancelled.

        Interval is ``lease_seconds / 3`` (min 1s) so two renews of headroom fit
        inside one lease. Stops early if a renew is a no-op — the lease was already
        lost to another worker, so there is nothing left to keep alive.
        """
        interval = max(1.0, self._config.lease_seconds / 3)
        while True:
            await asyncio.sleep(interval)
            renewed = await self._db(
                self._repo.renew_lease,
                task_id=task_id,
                worker_id=self._worker_id,
                lease_seconds=self._config.lease_seconds,
            )
            if not renewed:
                logger.warning(
                    "[TaskWorker] lease renew for task id=%s was a no-op — claim "
                    "lost to another worker; stopping heartbeat",
                    task_id,
                )
                return

    async def _apply(self, task: TaskRecord, outcome) -> None:
        if isinstance(outcome, Complete):
            await self._write_outcome(
                self._repo.complete, task_id=task.id, worker_id=self._worker_id
            )
            return

        if isinstance(outcome, Fail):
            await self._write_outcome(
                self._repo.fail,
                task_id=task.id,
                worker_id=self._worker_id,
                error=outcome.error,
            )
            return

        # Reschedule / Retry both re-pend the task with a delay. The repository
        # decides DB-side whether the new run_at overshoots the deadline (→
        # TIMED_OUT), so the worker never does deadline math.
        if isinstance(outcome, Reschedule):
            delay = outcome.delay_seconds   # caller-chosen poll cadence
            error = None
        elif isinstance(outcome, Retry):
            delay = self._backoff(task.attempts)  # always backoff for retries
            error = outcome.error
        else:  # pragma: no cover - defensive; outcomes are a closed union
            raise TypeError(f"unknown TaskOutcome: {outcome!r}")

        await self._write_outcome(
            self._repo.reschedule,
            task_id=task.id,
            worker_id=self._worker_id,
            delay_seconds=delay,
            error=error,
        )

    def _backoff(self, attempts: int) -> float:
        """Exponential backoff from the (already-incremented) attempt count,
        bounded by [min, max]. attempts>=1 on the first retry → ``min``, then
        doubles each retry, capped at ``max``. Retries are bounded by the
        deadline, not an attempt count."""
        floor = self._config.retry_backoff_min_seconds
        cap = self._config.retry_backoff_max_seconds
        exp = max(0, attempts - 1)
        return min(floor * (2 ** exp), cap)
