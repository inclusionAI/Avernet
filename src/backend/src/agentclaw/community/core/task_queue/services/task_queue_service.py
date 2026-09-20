"""Enqueue facade — the programmatic entry point adopters call.

Thin wrapper over the repository. Timing is owned by the database: this service
just forwards the relative ``delay_seconds`` / ``deadline_seconds`` durations
(the repository turns them into absolute ``run_at`` / ``deadline_at`` with the
DB clock) and stamps the current ``env`` and ``app``.

``app`` names the deployment that owns the row in the shared ``ac_task_queue``
table and comes from config (:class:`TaskQueueConfig`), never from the caller —
it must match what this deployment's :class:`TaskWorker` claims with, and an
adopter has no way to know that.

It also carries the one piece of policy the repository has no business knowing:
whether an enqueue should wake the in-process worker immediately (see
:class:`WorkerWakeup`) instead of leaving it to the next idle poll. That is
opt-in per task type, declared at handler registration.
"""

from __future__ import annotations

from injector import inject

from typing import Optional

from agentclaw.community.core.repository.protocols.platform import (
    TaskQueueRepositoryProtocol,
)
from agentclaw.community.core.task_queue.services.registry import HandlerRegistry
from agentclaw.community.core.task_queue.services.wakeup import WorkerWakeup
from agentclaw.community.core.task_queue.types import (
    MAX_TRACE_ID_LEN,
    EnqueueResult,
    TaskRecord,
)
from agentclaw.community.di.config import TaskQueueConfig
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.tracer import TracerPlugin
from agentclaw.community.utils.env_utils import get_current_env

logger = get_logger()


class TaskQueueService:
    """Persist background work for the in-process worker to pick up."""

    @inject
    def __init__(
        self,
        repo: TaskQueueRepositoryProtocol,
        registry: HandlerRegistry,
        wakeup: WorkerWakeup,
        config: TaskQueueConfig,
        tracer: TracerPlugin,
    ) -> None:
        self._repo = repo
        self._registry = registry
        self._wakeup = wakeup
        self._config = config
        self._tracer = tracer

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
          **live** task exists per key within this ``(env, app, task_type)``: a
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

        **Trace correlation.** The calling request's trace is captured here and
        stored on the row, and ``TaskWorker`` re-establishes it around the
        handler — so a task enqueued by an open-API call logs under that call's
        trace id when it actually runs, however much later and on whichever pod.
        Nothing is asked of callers: this is the one place every enqueue passes
        through, which is exactly why the capture lives here rather than at the
        ~25 call sites. Outside a request (a handler enqueuing follow-up work,
        a boot-time enqueue) there is no trace to capture and the columns stay
        ``NULL``.

        See ``TaskQueueRepositoryProtocol.enqueue`` for the key convention and
        the full contract.
        """
        trace_id, trace_carrier = self._capture_trace()
        result = self._repo.enqueue(
            task_type=task_type,
            payload=payload,
            delay_seconds=delay_seconds,
            deadline_seconds=deadline_seconds,
            env=get_current_env(),
            app=self._config.app,
            idempotency_key=idempotency_key,
            trace_id=trace_id,
            trace_carrier=trace_carrier,
        )
        if self._should_wake(result, task_type=task_type, delay_seconds=delay_seconds):
            # Signalled only *after* the repository call returns, which matters:
            # ``orm_session()`` commits on clean exit, so the row is committed
            # and visible to any claim the wake triggers. Signalling earlier
            # would race the worker against our own uncommitted insert.
            self._wakeup.notify()
        return result

    def _capture_trace(self) -> tuple[Optional[str], Optional[dict]]:
        """The current trace as ``(id, carrier)``, or ``(None, None)``.

        **Swallows everything.** Correlation is a diagnostic, and a diagnostic
        that can fail an enqueue is worse than no diagnostic at all: the work
        the caller asked for would be lost to a tracer problem. The protocol
        already says an impl must not raise, so this is defence in depth against
        a future one that does — including a corp SDK that throws from deep
        inside its own context handling.

        The id is truncated rather than rejected for the same reason, and unlike
        ``idempotency_key``, which is validated and *raises*. The difference is
        what each column does: a truncated key silently merges two distinct
        dedup scopes and can drop a caller's work, while a truncated trace id is
        merely a less useful log line. Both engines would otherwise disagree
        about the overflow (SQLite ignores the width, strict MySQL raises,
        non-strict truncates), so the value is bounded here where the behaviour
        is the same everywhere.
        """
        try:
            trace_id = self._tracer.current_trace_id()
            carrier = self._tracer.export_trace_carrier()
        except Exception:
            logger.warning(
                "[task_queue.enqueue] tracer failed to export a trace context; "
                "enqueueing without correlation",
                exc_info=True,
            )
            return None, None
        if trace_id and len(trace_id) > MAX_TRACE_ID_LEN:
            logger.warning(
                "[task_queue.enqueue] trace id longer than %d chars; storing truncated",
                MAX_TRACE_ID_LEN,
            )
            trace_id = trace_id[:MAX_TRACE_ID_LEN]
        return trace_id or None, carrier or None

    def find_by_idempotency_key(
        self, task_type: str, idempotency_key: str
    ) -> Optional[TaskRecord]:
        """What became of the work submitted under this key. A read, nothing else.

        The one read on this facade, and it is here for the same reason
        :meth:`enqueue` is: ``env`` and ``app`` scope the key, both come from
        deployment config, and an adopter has no way to supply them. Reaching
        the repository directly would mean guessing them.

        Answers for a **live** task and for a terminal one alike — which is the
        point, since the questions worth asking ("did it fail? did it run out of
        time?") are about a task that is over, and a terminal transition
        releases the key so ``enqueue`` can no longer see it.

        ``None`` means no task was ever enqueued under this key in this
        ``(env, app, task_type)``, not that one finished. See
        ``TaskQueueRepositoryProtocol.find_by_idempotency_key`` for the cost
        model — cheap while the task is live, a scan once it is over — and use
        it accordingly.
        """
        return self._repo.find_by_idempotency_key(
            task_type=task_type,
            idempotency_key=idempotency_key,
            env=get_current_env(),
            app=self._config.app,
        )

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
