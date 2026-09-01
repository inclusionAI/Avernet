"""Applying a manifest, as a durable task rather than a daemon thread (W13).

W4 ran an apply on ``threading.Thread(..., daemon=True)``. ``core/task_queue``'s
README names that pattern as the one it exists to replace — it "loses work on
restart and double-runs across pods" — and W13 makes the loss load-bearing:
**creation depends on an apply completing.** The startup-script row has to exist
before ``BaasService._build_create_bot_payload`` composes the start command, so a
thread that dies with its pod does not merely lose a report, it boots a bot
without its script.

So every apply now runs here, on all three paths — the pre-container phase, the
post-container phase, and the explicit ``POST …/config-manifest/apply`` on a
running bot. **One task type**, because the three differ only in arguments the
orchestrator does not branch on (`trigger` reaches it as a parameter it copies
onto the report, and nothing else reads it); the apply record's ``trigger`` column
is what distinguishes them for anyone querying.

**Re-running is safe because apply converges, not because retry is off.** The
queue is at-least-once *structurally*: a crashed worker's task is re-claimed once
its lease expires, whether or not a handler ever returns ``Retry``. Safety comes
from the apply itself — re-applying an unchanged document performs no writes, and
the lock serialises attempts. Anyone adding a materialiser that is not convergent
breaks this, and no amount of queue configuration would save it.

**What is *not* here.** The lock, the re-validation that raises to the caller, the
``apply_id`` and the ``RUNNING`` row all stay in ``start_apply``, synchronous on
the caller's thread — see its docstring. Only the work moved, which is what keeps
``POST …/config-manifest/apply``'s contract (``202`` + an id, a concurrent apply
refused, a validation failure raised) exactly as W4 shipped it.
"""
from __future__ import annotations

from typing import Any, Callable, Optional, Sequence

from agentclaw.community.core.bot_config_manifest.apply.order import ApplyPhase
from agentclaw.community.core.task_queue.services.registry import HandlerRegistry
from agentclaw.community.core.task_queue.types import Complete, TaskOutcome
from agentclaw.community.kernel.lifecycle import LifecycleBase
from agentclaw.community.log import get_logger

logger = get_logger()

#: The registry key. One type for all three cases (module docstring).
APPLY_TASK_TYPE = "config_manifest.apply"

#: Give-up horizon for one apply. Generous on purpose: this bounds a *task*, and
#: an apply that legitimately takes minutes (W5 fetching several sources) must
#: not be retired mid-write. It sits inside the apply lock's own TTL so a task
#: retired here cannot outlive the lock that a later apply would reap.
APPLY_TASK_DEADLINE_SECONDS = 20 * 60


def build_apply_task_payload(
    *,
    apply_id: str,
    entity_id: str,
    bot_id: str,
    owner_id: str,
    actor_id: str,
    env: str,
    tenant: str,
    trigger: str,
    lock_token: str,
    phases: Optional[frozenset[ApplyPhase]] = None,
    carry_from_apply_id: Optional[str] = None,
    engine_type: Optional[str] = None,
    bot_type: Optional[str] = None,
) -> dict[str, Any]:
    """The task payload: identifiers, never state.

    Two things are deliberately absent, and both would be bugs to add.

    **The parsed document.** ``MAX_DOCUMENT_BYTES`` is 64 KB and
    ``ac_task_queue.payload`` is ``Text`` — also 64 KB on MySQL — so a large
    manifest plus these fields would not fit, and a non-strict server truncates
    silently. The handler re-reads and re-validates instead. The consequence,
    stated rather than discovered: the document is read *as of execution* rather
    than snapshotted at enqueue, so a ``PUT`` landing in between is picked up. The
    window is short (this type wakes the worker immediately), a concurrent
    *apply* cannot interleave because the lock is held across the handoff, and
    re-reading is the level-triggered behaviour W4 already chose for its own
    re-validation.

    **The bot record.** It could be stale by the time the task runs, and it is not
    small. The handler re-reads it. ``engine_type`` / ``bot_type`` are carried for
    the one case where there is no record to read — W13's pre-container phase runs
    *before* the bot is created — and are ignored when a record exists.

    ``tenant`` is carried because the queue has no tenant column and no request
    context survives to handler time; see the handler for why that is a test and
    not a comment.
    """
    return {
        "apply_id": apply_id,
        "entity_id": entity_id,
        "bot_id": bot_id,
        "owner_id": owner_id,
        "actor_id": actor_id,
        "env": env,
        "tenant": tenant,
        "trigger": trigger,
        "lock_token": lock_token,
        # Sorted for a stable payload: two enqueues of the same apply must not
        # differ only by set iteration order.
        "phases": sorted(p.value for p in phases) if phases is not None else None,
        "carry_from_apply_id": carry_from_apply_id,
        "engine_type": engine_type,
        "bot_type": bot_type,
    }


def phases_from_payload(
    value: Optional[Sequence[str]],
) -> Optional[frozenset[ApplyPhase]]:
    """``None`` means both phases — the same convention ``steps_for`` uses."""
    if value is None:
        return None
    return frozenset(ApplyPhase(item) for item in value)


class ApplyTaskHandler:
    """Runs one apply. A thin adapter over the service that owns the lifecycle.

    The body lives in ``BotConfigManifestApplyService.run_apply_task`` rather than
    here because the lock, the record and the orchestrator are that service's, and
    splitting them across a handler would give the apply two owners.

    **Outcomes.** ``Complete`` once the apply has recorded an outcome — including
    when the apply *failed*, because a failed apply is a report, not a failed
    task. A raise from the service (the database is unreachable, say) propagates,
    and the worker treats that as a retry with backoff: the right answer, since
    the apply had no chance to record anything and re-running it converges.
    """

    def __init__(
        self,
        apply_service_provider: Callable[[], Any],
    ) -> None:
        # A lazy provider, matching the sibling services: the apply service
        # reaches back into this package, and holding a concrete instance would
        # close an import cycle at construction.
        self._apply_service_provider = apply_service_provider

    @property
    def task_type(self) -> str:
        return APPLY_TASK_TYPE

    def handle(self, payload: Optional[dict]) -> TaskOutcome:
        if not payload:
            # Nothing to run and nothing to release. A payload-less apply task is
            # a wiring bug; completing it stops the queue retrying it forever.
            logger.error("[manifest_apply_task] empty payload, nothing to run")
            return Complete()
        self._apply_service_provider().run_apply_task(payload)
        return Complete()


class ApplyTaskLifecycle(LifecycleBase):
    """Registers the handler at boot.

    ``wake_on_enqueue=True``: somebody is waiting on every one of these — a caller
    polling ``apply_id``, or the creation job that cannot create the bot until the
    pre-container phase has finished. Waiting out an idle poll interval would add
    latency to a user-visible operation for no benefit.
    """

    def __init__(
        self,
        *,
        registry: HandlerRegistry,
        apply_service_provider: Callable[[], Any],
    ) -> None:
        self._registry = registry
        self._apply_service_provider = apply_service_provider

    async def bootstrap(self) -> None:
        self._registry.register(
            ApplyTaskHandler(self._apply_service_provider),
            wake_on_enqueue=True,
        )
        logger.info("[manifest_apply_task] registered %s", APPLY_TASK_TYPE)


__all__ = [
    "APPLY_TASK_DEADLINE_SECONDS",
    "APPLY_TASK_TYPE",
    "ApplyTaskHandler",
    "ApplyTaskLifecycle",
    "build_apply_task_payload",
    "phases_from_payload",
]
