"""Enqueue facade — the programmatic entry point adopters call.

Thin wrapper over the repository. Timing is owned by the database: this service
just forwards the relative ``delay_seconds`` / ``deadline_seconds`` durations
(the repository turns them into absolute ``run_at`` / ``deadline_at`` with the
DB clock) and stamps the current ``env``.

It also carries the one piece of policy the repository has no business knowing:
whether an enqueue should wake the in-process worker immediately (see
:class:`WorkerWakeup`) instead of leaving it to the next idle poll. That is
opt-in per task type, declared at handler registration.
"""
from __future__ import annotations

from injector import inject

from typing import Optional

from agentclaw.community.core.repository.protocols.platform import TaskQueueRepositoryProtocol
from agentclaw.community.core.task_queue.services.registry import HandlerRegistry
from agentclaw.community.core.task_queue.services.wakeup import WorkerWakeup
from agentclaw.community.core.task_queue.types import EnqueueResult
from agentclaw.community.utils.env_utils import get_current_env


class TaskQueueService:
    """Persist background work for the in-process worker to pick up."""

    @inject
    def __init__(
        self,
        repo: TaskQueueRepositoryProtocol,
        registry: HandlerRegistry,
        wakeup: WorkerWakeup,
    ) -> None:
        self._repo = repo
        self._registry = registry
        self._wakeup = wakeup

    def enqueue(
        self,
        task_type: str,
        payload: dict,
        deadline_seconds: int,
        *,
        delay_seconds: int = 0,
        idempotency_key: Optional[str] = None,
    ) -> EnqueueResult:
        """Enqueue a task. Returns ``(record, created)``.

        - ``task_type`` — the registry key whose handler will run it.
        - ``payload`` — the (required) work description; persisted as JSON.
        - ``deadline_seconds`` — give-up horizon from now; every task must have
          one. Past it, the task is retired ``TIMED_OUT`` (enforced DB-side).
        - ``delay_seconds`` — how long until the task first becomes eligible
          (``run_at = now + delay``); ``0`` (default) means immediately.
        - ``idempotency_key`` — opt-in submission dedup. With a key, at most one
          **live** task exists per key within this ``(env, task_type)``: a
          duplicate enqueue inserts nothing and returns the live task with
          ``created=False``. Terminal tasks release their key, so a retry or a
          later re-run of the same logical work is *not* suppressed. Omit it
          (the default) for work that should always produce a distinct row —
          recurring polls, timers, genuine fan-out. Must be non-empty, at most
          190 characters (the stored column width), and free of leading or
          trailing whitespace; all three raise ``ValueError`` rather than
          risking a silent collision of two distinct keys on MySQL/OceanBase
          (truncation under a non-strict server, space padding under the
          collation).

        **Immediate execution.** If ``task_type`` was registered with
        ``wake_on_enqueue=True`` and this call created a task that is due now,
        the in-process worker is signalled to poll at once rather than waiting
        out its idle interval. Every other task type is unaffected. The signal
        is best-effort latency only — it never changes which task runs, who
        claims it, or what happens if it is missed; a missed signal just means
        the ordinary poll picks the task up.

        See ``TaskQueueRepositoryProtocol.enqueue`` for the key convention and
        the full contract.
        """
        result = self._repo.enqueue(
            task_type=task_type,
            payload=payload,
            delay_seconds=delay_seconds,
            deadline_seconds=deadline_seconds,
            env=get_current_env(),
            idempotency_key=idempotency_key,
        )
        if self._should_wake(result, task_type=task_type, delay_seconds=delay_seconds):
            # Signalled only *after* the repository call returns, which matters:
            # ``orm_session()`` commits on clean exit, so the row is committed
            # and visible to any claim the wake triggers. Signalling earlier
            # would race the worker against our own uncommitted insert.
            self._wakeup.notify()
        return result

    def _should_wake(
        self, result: EnqueueResult, *, task_type: str, delay_seconds: int
    ) -> bool:
        """Whether this enqueue should cut short the worker's idle wait.

        Three conditions, all required:

        - **The type opted in.** Default-off, so existing task types keep their
          current timing (see ``HandlerRegistry.register``).
        - **The task is due now.** A delayed task has ``run_at > now()`` and so
          fails the claim's eligibility predicate; waking for it would burn a
          poll and change nothing.
        - **A task was actually created.** A keyed enqueue that joined a live
          holder (``created=False``) added no work — the holder is already
          pending or running, and waking cannot make a future ``run_at``
          eligible any sooner.
        """
        if delay_seconds > 0 or not result.created:
            return False
        return self._registry.wakes_on_enqueue(task_type)
