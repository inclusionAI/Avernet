"""Durable Bot-level skill activation synchronization task.

A skill activation is two writes that must land together: the desired-state
change in the database and the RPC that repoints the Bot's runtime symlinks.
``SkillSetManagementService._mutate`` performs them inline today and, when the
RPC fails, compensates by restoring the previous desired state and reconciling
again — a best-effort path that a pod restart between the two writes defeats
outright. This module is the enqueue half of the durable replacement the
skill_center README defers to ("durable serialization is deferred to the
task-queue design"): the operation is persisted as a task *first*, so a crash
resumes it instead of stranding the Bot with desired state its runtime never
received.

**The handler here is a skeleton.** :class:`SkillActivationSyncTaskHandler`
declares the task type, parses and validates the payload, and then hands off to
:meth:`SkillActivationSyncTaskHandler._run` — the single seam the follow-up
fills in. ``_run`` currently reports :class:`~agentclaw.community.core.task_queue.types.Fail`,
and it is not registered into a :class:`~agentclaw.community.core.task_queue.services.registry.HandlerRegistry`;
registration lands with the body, alongside the first operation that enqueues.
Until then nothing calls :func:`enqueue_skill_activation_sync`, so no row of this
type exists to run.

Two properties of the contract are worth reading before adopting it.

**The dedup key is the Bot, not the operation.** The key is
``(env, entity_id, bot_id)`` and deliberately excludes ``action_type``: the unit
that must not be synchronized concurrently is the Bot's runtime projection, and
two different operations racing on one Bot are exactly the case the durable path
exists to serialize. The consequence is load-bearing for callers — a second
operation arriving while a sync is live does **not** enqueue: it joins the live
task and :class:`~agentclaw.community.core.task_queue.types.EnqueueResult`
reports ``created=False``. That is correct for a level-triggered handler, which
reconciles against whatever desired state the database holds when it runs and so
picks up the newer operation's write for free. It would silently drop work for a
handler that performed the desired-state write *itself* from ``action_args`` —
so the handler this task is built for must read desired state from the database,
not from the payload. The payload's ``action_type`` says which operation asked
for the sync; it is not a command to replay.

**The key is scoped by the Bot's ``env``, not the worker's.** ``TaskQueueService``
stamps the row's ``env`` column from the current process, while the key carries
the ``env`` of the Bot being synchronized. The two normally agree; where they do
not, the Bot's own environment is the one that identifies the work.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import StrEnum

from agentclaw.community.core.task_queue.services.task_queue_service import (
    TaskQueueService,
)
from agentclaw.community.core.task_queue.types import (
    EnqueueResult,
    Fail,
    TaskOutcome,
)

#: Registry key for this task. ``<module>.<name>``, matching the convention used
#: by ``skills_pool.reconcile`` and ``session_resource.materialize``.
SKILL_ACTIVATION_SYNC_TASK = "skill_center.activation_sync"

#: Give-up horizon for one synchronization, in seconds.
#:
#: Shorter than the day-long horizons used by publish and Pool migration, and
#: for a reason specific to a keyed task: a live task *holds* its dedup key, so
#: every subsequent activation on that Bot joins it instead of enqueuing. The
#: deadline is therefore not just "how long to keep retrying" but "how long a
#: wedged sync may keep suppressing the ones behind it". Fifteen minutes is far
#: past the point where an interactive activation is still worth converging, and
#: retiring the task ``TIMED_OUT`` releases the key so the next operation
#: enqueues cleanly.
SKILL_ACTIVATION_SYNC_DEADLINE_SECONDS = 15 * 60

#: Hex characters of the SHA-256 digest kept in the dedup key. 128 bits — a
#: collision would need two distinct Bots, and it is bounded so the key cannot
#: outgrow its column. See :func:`build_skill_activation_sync_idempotency_key`.
_KEY_DIGEST_CHARS = 32

#: Separator for the digest input. A control character rather than ``:`` so that
#: no ``entity_id`` containing the visible separator can be rearranged into
#: another Bot's digest input.
_DIGEST_SEPARATOR = "\x1f"


class SkillActivationSyncAction(StrEnum):
    """Which operation asked for the synchronization.

    Persisted in the payload as ``action_type`` because one task type is shared
    by every skill-activation-shaped operation (activate, deactivate, switch,
    membership changes, MCP direct installs), and the handler has to know which
    one it is executing.

    Only a placeholder member exists so far — the operations adopt this task as
    they migrate onto the durable path, and each adoption adds its member here.
    Adding one is a one-line edit; the parse, validation, and enqueue plumbing
    below is already generic over the enum.

    **Rollout note.** :func:`parse_skill_activation_sync_payload` rejects an
    ``action_type`` it does not recognise, so a task enqueued by a newer pod is
    unrunnable on an older one. Ship a new member in a release before the
    release that starts enqueuing it, the same ordering the task queue's own
    idempotency mechanism required of its first adopter.
    """

    #: Not a real operation. Exists so the discriminator has a member — and a
    #: test can exercise the payload contract — before the first operation
    #: migrates. Remove it once a real member exists.
    PLACEHOLDER = "placeholder"


@dataclass(frozen=True, slots=True)
class SkillActivationSyncScope:
    """The Bot whose runtime projection is being synchronized.

    Bot-level is the granularity the durable path serializes at: one runtime
    projection, one symlink tree, one in-flight sync.

    Structurally identical to :class:`~agentclaw.community.core.skills_pool.types.BotSkillLayoutScope`
    and deliberately not that type. Layout scope identifies a row of Pool
    migration state; this identifies the target of a Skill Center operation.
    They agree on the triple today because the same three facts name a Bot, not
    because either owns the other's meaning.
    """

    env: str
    entity_id: str
    bot_id: str


@dataclass(frozen=True, slots=True)
class SkillActivationSyncWork:
    """A parsed payload — what the handler receives after validation."""

    scope: SkillActivationSyncScope
    action: SkillActivationSyncAction
    #: Operation-specific arguments (a set id, a skill id, …). Audit and
    #: routing detail for the handler; see the module docstring for why it must
    #: not be replayed as the desired-state write. Empty when the operation
    #: needs none — never absent.
    action_args: dict[str, object]


def build_skill_activation_sync_idempotency_key(
    scope: SkillActivationSyncScope,
) -> str:
    """Build the enqueue dedup key for ``scope``.

    Shape: ``skill_activation_sync:<env>:<digest>``, where ``digest`` is the
    first :data:`_KEY_DIGEST_CHARS` hex characters of the SHA-256 of
    ``entity_id`` and ``bot_id``.

    The identity is the ``(env, entity_id, bot_id)`` triple; the digest is only
    how two of the three are *spelled*. They are hashed rather than embedded
    because the key column is ``VARCHAR(190)`` while its sources are far wider —
    ``entity_id`` is ``varchar(512)`` and ``bot_id`` ``varchar(128)`` on the
    Skills Pool tables — and the task queue README is explicit that the variable
    part of a key should be hashed rather than embedded for exactly this reason.
    Embedding them would fit for every id in production today and raise
    ``ValueError`` mid-activation on the first tenant whose id is long, which is
    a worse failure than an opaque segment: a key that cannot be built is an
    activation that cannot proceed, whereas a digest always fits. ``env`` stays
    readable because it is ``varchar(20)`` and is what an operator scanning
    ``ac_task_queue`` filters on first; the raw triple is preserved verbatim in
    the payload, so nothing is lost to audit. To find a specific Bot's task, call
    this function rather than eyeballing the column.

    Raises ``ValueError`` if any component is empty or carries surrounding
    whitespace. That mirrors the repository's own key validation and applies it
    one level up, where the offending field can still be named: a padded
    ``env`` would otherwise reach the queue as a padded key and be rejected
    there with no clue which part of the scope was at fault.
    """
    for name, value in (
        ("env", scope.env),
        ("entity_id", scope.entity_id),
        ("bot_id", scope.bot_id),
    ):
        if not value or not value.strip():
            raise ValueError(f"scope.{name} must be a non-empty string")
        if value != value.strip():
            raise ValueError(
                f"scope.{name} must not have leading or trailing whitespace "
                f"({value!r})"
            )
    digest = hashlib.sha256(
        f"{scope.entity_id}{_DIGEST_SEPARATOR}{scope.bot_id}".encode()
    ).hexdigest()[:_KEY_DIGEST_CHARS]
    return f"skill_activation_sync:{scope.env}:{digest}"


def build_skill_activation_sync_payload(
    *,
    scope: SkillActivationSyncScope,
    action: SkillActivationSyncAction,
    action_args: dict[str, object] = {},
) -> dict[str, object]:
    """Build the persisted work description.

    The payload carries only what identifies the work: which Bot, which
    operation, and that operation's arguments. It deliberately holds no
    generated correlation id — a fresh UUID would correlate to nothing the
    caller knows, and ``ac_task_queue.id`` is already the task's identity.

    ``action_args`` is copied rather than stored by reference: the empty default
    is shared across calls, and the caller's dict must not be able to change a
    payload after it is built.
    """
    return {
        "scope": {
            "env": scope.env,
            "entity_id": scope.entity_id,
            "bot_id": scope.bot_id,
        },
        "action_type": action.value,
        "action_args": dict(action_args),
    }


def parse_skill_activation_sync_payload(payload: dict) -> SkillActivationSyncWork:
    """Validate a persisted payload and return the work it describes.

    Raises ``ValueError`` on anything malformed, including an unrecognised
    ``action_type``. The handler is expected to turn that into
    :class:`~agentclaw.community.core.task_queue.types.Fail` rather than
    :class:`~agentclaw.community.core.task_queue.types.Retry`: a payload that
    cannot be understood will not become understandable on the next attempt, and
    retrying it would hold the Bot's dedup key until the deadline while blocking
    every subsequent activation behind a row that can never run.
    """
    raw_scope = payload.get("scope")
    if not isinstance(raw_scope, dict):
        raise ValueError("scope must be an object")
    scope_values: dict[str, str] = {}
    for key in ("env", "entity_id", "bot_id"):
        value = raw_scope.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"scope.{key} must be a non-empty string")
        scope_values[key] = value

    raw_action = payload.get("action_type")
    if not isinstance(raw_action, str) or not raw_action:
        raise ValueError("action_type must be a non-empty string")
    try:
        action = SkillActivationSyncAction(raw_action)
    except ValueError as error:
        raise ValueError(f"unknown action_type {raw_action!r}") from error

    raw_args = payload.get("action_args")
    if not isinstance(raw_args, dict):
        raise ValueError("action_args must be an object")

    return SkillActivationSyncWork(
        scope=SkillActivationSyncScope(**scope_values),
        action=action,
        action_args=dict(raw_args),
    )


def enqueue_skill_activation_sync(
    task_queue_service: TaskQueueService,
    *,
    scope: SkillActivationSyncScope,
    action: SkillActivationSyncAction,
    action_args: dict[str, object] = {},
    deadline_seconds: int = SKILL_ACTIVATION_SYNC_DEADLINE_SECONDS,
    delay_seconds: int = 0,
) -> EnqueueResult:
    """Enqueue one durable synchronization for ``scope``, tagged with ``action``.

    Returns the :class:`~agentclaw.community.core.task_queue.types.EnqueueResult`
    unchanged — **check ``created``**. ``False`` means this Bot already had a
    sync in flight and the returned record is that task, not a new one; see the
    module docstring for why that is the intended outcome and what it obliges
    the handler to do. Callers that surface a result to a user should treat
    ``created=False`` as "already syncing", not as a failure.

    A plain function rather than an injected service: every prospective call site
    already holds a ``TaskQueueService``, and the enqueue carries no state of its
    own beyond the constants in this module.

    Enqueue before the external side effect, not after. The task is the record
    that the operation was requested; if persisting it fails, nothing has
    happened yet and the caller can report the failure honestly.
    """
    return task_queue_service.enqueue(
        SKILL_ACTIVATION_SYNC_TASK,
        build_skill_activation_sync_payload(
            scope=scope,
            action=action,
            action_args=action_args,
        ),
        deadline_seconds=deadline_seconds,
        delay_seconds=delay_seconds,
        idempotency_key=build_skill_activation_sync_idempotency_key(scope),
    )


class SkillActivationSyncTaskHandler:
    """Runs one Bot-level skill activation synchronization.

    A plain object, as the :class:`~agentclaw.community.core.task_queue.services.registry.TaskHandler`
    protocol expects — no base class, just ``task_type`` and ``handle``.

    **The work itself is not implemented.** What is settled here is the shape
    the implementation plugs into: the registry key, the payload validation
    every attempt must pass, and the outcome the queue sees when the work
    cannot proceed. :meth:`_run` is the seam.
    """

    @property
    def task_type(self) -> str:
        return SKILL_ACTIVATION_SYNC_TASK

    def handle(self, payload: dict) -> TaskOutcome:
        """Validate the payload, then run the operation it describes.

        Takes ``dict`` rather than the protocol's ``Optional[dict]``:
        ``TaskRecord.payload`` is non-optional and the worker only ever passes
        a persisted row's deserialized payload. The protocol is structural, so
        the narrower annotation still satisfies it.
        """
        try:
            work = parse_skill_activation_sync_payload(payload)
        except ValueError as error:
            # Fail, not Retry. A payload that cannot be parsed will not parse
            # on the next attempt either, and retrying would pin the Bot's
            # dedup key until the deadline — blocking every later activation
            # behind a row that can never run.
            return Fail(f"invalid skill activation sync payload: {error}")
        return self._run(work)

    def _run(self, work: SkillActivationSyncWork) -> TaskOutcome:
        """Execute one synchronization. **Not implemented yet.**

        The follow-up fills this in: branch on ``work.action`` to identify the
        operation, then converge ``work.scope``'s runtime projection against
        the desired state held in the database — not against
        ``work.action_args``, for the joined-task reason in the module
        docstring.

        Returning :class:`~agentclaw.community.core.task_queue.types.Fail` is
        deliberate, rather than raising ``NotImplementedError``: the worker
        treats a raise as an implicit ``Retry`` with backoff, which would hold
        the Bot's dedup key for the full deadline and suppress the activations
        queued behind it. ``Fail`` is terminal, so the key is released at once
        and the error is recorded on the row.
        """
        return Fail(
            "skill_center.activation_sync has no implementation yet "
            f"(action_type={work.action.value}, bot_id={work.scope.bot_id})"
        )


__all__ = [
    "SKILL_ACTIVATION_SYNC_DEADLINE_SECONDS",
    "SKILL_ACTIVATION_SYNC_TASK",
    "SkillActivationSyncAction",
    "SkillActivationSyncScope",
    "SkillActivationSyncTaskHandler",
    "SkillActivationSyncWork",
    "build_skill_activation_sync_idempotency_key",
    "build_skill_activation_sync_payload",
    "enqueue_skill_activation_sync",
    "parse_skill_activation_sync_payload",
]
