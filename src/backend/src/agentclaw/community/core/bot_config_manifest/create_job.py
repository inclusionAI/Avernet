"""The job that carries a create-from-manifest through to a configured bot (W13).

Submission answers `202` and stops. Everything after — waiting for the user to
authorize, applying the pre-container phase, creating the bot, waiting for its
container, starting the post-container phase — happens here, on a durable task.

**Why a job and not the poll.** A poll-driven creation stalls forever the moment
a caller walks away, and "the user clicked the link an hour later" is an ordinary
case. The job also gives abandonment a terminal state: the queue's wall-clock
deadline retires it as ``TIMED_OUT``, which the poll reports as expired.

**Re-entrancy, and the reason it is not optional.** The queue guarantees a single
claimer but **at-least-once invocation**: a crashed worker's task is re-claimed
once its lease expires, whether or not a handler ever returns ``Retry``. So every
step asks "is this already done?" against durable state rather than tracking a
cursor. Two things underneath make that safe rather than merely careful —
``create_bot`` is idempotent on a supplied ``bot_id`` (it returns the existing
bot), and ``start_apply`` takes the apply lock.

**It waits for the pre-container phase and not the post-container one**, which is
not an oversight. Creation *depends* on the first: the startup-script row must
exist before the start command is composed. Nothing depends on the second — which
is exactly true of an apply against an already-running bot too — so the job starts
it and finishes, and the poll observes it the way it observes any apply.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, Callable, Optional

from agentclaw.community.core.bot_config_manifest.apply.delivery import (
    CreationSequence,
)
from agentclaw.community.core.bot_config_manifest.apply.order import ApplyPhase
from agentclaw.community.core.bot_config_manifest.apply.outcomes import ApplyStatus
from agentclaw.community.core.bot_config_manifest.bot_config_manifest_apply_service_protocol import (
    ManifestApplyInProgressError,
)
from agentclaw.community.core.bot_config_manifest.bot_config_manifest_apply_service_protocol import (
    BotConfigManifestApplyServiceProtocol,
)
from agentclaw.community.core.bot_config_manifest.creation import (
    CREATE_ON_CONTAINER_TRIGGER,
    CREATE_PRE_CONTAINER_TRIGGER,
)
from agentclaw.community.core.bot_management.manifest_seam import (
    ManifestCreationSeam,
)
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.plugin_api.passport import PassportPlugin
from agentclaw.community.core.task_queue.services.registry import HandlerRegistry
from agentclaw.community.core.task_queue.types import (
    Complete,
    Fail,
    Reschedule,
    TaskOutcome,
    TaskRecord,
)
from agentclaw.community.kernel.lifecycle import LifecycleBase
from agentclaw.community.log import get_logger
from agentclaw.community.utils.avernet_tenant import avernet_tenant_scope

if TYPE_CHECKING:  # pragma: no cover - import-time cycle, see ``enqueue_create_job``
    from agentclaw.community.core.task_queue.services.task_queue_service import (
        TaskQueueService,
    )

logger = get_logger()

#: The registry key for a creation.
CREATE_JOB_TASK_TYPE = "config_manifest.create_bot"

#: How long a user has to follow the authorization link before the creation is
#: retired.
#:
#: **The default, not the setting.** The live value is
#: ``BotCreateWithManifestConfig.authorization_window_seconds``, read from
#: ``user_config.bot_create_with_manifest`` and handed to ``enqueue_create_job``
#: by the caller — this module reaches for no configuration of its own. It is
#: still needed here as the fallback for a payload enqueued before
#: ``window_seconds`` was written into it.
DEFAULT_CREATE_DEADLINE_SECONDS = 10 * 60

#: How much longer the queue's own deadline is than the authorization window.
#:
#: **The gap is load-bearing, not slack.** The queue retires a past-deadline task
#: *in its claim scan*, DB-side, without ever running the handler — and the
#: handler is the only thing that deletes the manifest and startup-script rows
#: submission wrote. With both horizons equal the queue always won the race, the
#: handler's own expiry check was unreachable, and an abandoned creation left its
#: rows behind: exactly the orphan class the deadline exists to bound.
#:
#: So the handler notices first (within one ``POLL_DELAY_SECONDS`` of the window)
#: and cleans up, and the queue's deadline stays what it was meant to be — the
#: outer backstop for a job that somehow stops making progress.
CREATE_QUEUE_DEADLINE_MARGIN_SECONDS = 10 * 60

#: The ``Fail`` reason a creation carries when its authorization window elapsed
#: rather than being declined.
#:
#: A shared constant, not a message, because the poll matches on it: expiry
#: normally reaches the handler first (``_expired``), which fails the task rather
#: than letting the queue retire it ``TIMED_OUT``, so the queue status alone
#: cannot tell "the user said no" from "the user never came back". Reporting the
#: first when the second happened would attribute to a user a decision they never
#: made. The queue's own ``TIMED_OUT`` is the other half of the same answer.
AUTHORIZATION_WINDOW_ELAPSED = "the authorization window elapsed"
#: The ``Fail`` reason's prefix when the bot could not be provisioned. Shared
#: with the poll for the same reason as the one above: under W8's
#: ``RECORD_APPLY_PROVISION`` sequence the service soft-deletes the record on an
#: allocation failure, so the poll sees no bot beside a terminal job — a
#: decline's shape — and only this prefix tells it the user did authorize.
BOT_COULD_NOT_BE_PROVISIONED = "the bot could not be provisioned"


def create_job_idempotency_key(
    *, tenant: str, entity_id: str, bot_id: str
) -> str:
    """The enqueue key for one creation. Derived, never stored.

    Both the submission (which enqueues) and the poll (which looks the job up)
    compute it from values they already hold, so there is nothing to keep in
    step and no column to add.

    It carries the same three parts as the manifest's own storage key, and for
    the same reason rather than for symmetry. The queue's dedup scope is
    ``(env, app, task_type)`` and knows nothing about tenants or owners, so
    anything left out of the key is something the lookup does not scope by —
    and the poll's whole authorization argument is that a caller can only reach
    rows keyed by the ``entity_id`` resolved from their own principal. Without
    ``entity_id`` in here, another user's ``bot_id`` would find their pending
    creation and hand back their authorization URL.

    ``bot_id`` alone is documented as globally unique, so the other two parts
    never change *which* row is found for a legitimate caller; they decide which
    rows are reachable at all.
    """
    return f"create_with_manifest:{tenant}:{entity_id}:{bot_id}"


#: How often the job looks again. Matches the existing publish poller's cadence.
POLL_DELAY_SECONDS = 5.0

#: Bot statuses that mean the container is up and the post-container phase can
#: run. Anything else is still provisioning.
_CONTAINER_READY_STATUSES = frozenset({"ACTIVE"})
#: Statuses that mean no container is ever coming.
_CONTAINER_FAILED_STATUSES = frozenset({"FAILED", "DELETED", "INACTIVE"})


def build_create_job_payload(
    *,
    bot_id: str,
    entity_id: str,
    user_id: str,
    tenant: str,
    env: str,
    document_owner: str,
    spec: dict[str, Any],
    iframe_url: Optional[str],
    redirect_url: Optional[str],
    window_seconds: int,
    submitted_at: Optional[str] = None,
    creation_sequence: Optional[str] = None,
) -> dict[str, Any]:
    """Everything the job needs, because nothing else will be available.

    The creation attributes are **materialised here**, at submission, which is
    what lets the poll take a ``bot_id`` and nothing else: the server already
    holds what the creation was for, so there is no echo to disagree with what
    was validated.

    The authorization handles ride along so the poll can return them without
    asking Passport again — a poll that queried an external service would be
    doing business work, and the job is already the thing that polls Passport.

    ``tenant`` is here because the queue has no tenant column and no request
    context survives to handler time.

    ``creation_sequence`` (W8) is the order the creation runs in, frozen at
    submission like the authorization window: a job that has already written
    an unprovisioned record must keep provisioning it even if the deployment
    switch that chose the sequence is flipped mid-creation.
    """
    payload = {
        "bot_id": bot_id,
        "entity_id": entity_id,
        "user_id": user_id,
        "tenant": tenant,
        "env": env,
        "document_owner": document_owner,
        "spec": spec,
        "iframe_url": iframe_url,
        "redirect_url": redirect_url,
        # When the window started. The handler owns expiry rather than leaving
        # it to the queue's deadline, and the difference is load-bearing: the
        # queue retires a task **DB-side, in its claim scan**, so the handler
        # never runs again and nothing would delete the rows submission wrote.
        # The queue's deadline stays as the outer backstop.
        "submitted_at": submitted_at or datetime.now().isoformat(),
        # How long this creation's window is, frozen at enqueue.
        #
        # **Read from here, never re-read from the environment.** The queue's
        # deadline is computed from this value once, at enqueue; if the handler
        # asked the environment again on each invocation the two could disagree
        # for a task already in flight. Raising the setting mid-deployment would
        # leave existing tasks with the *old*, shorter queue deadline, so the
        # database would retire them TIMED_OUT before the handler's new window
        # elapsed — and the handler is the only thing that deletes the rows
        # submission wrote. Lowering it would expire live requests early.
        # Freezing it makes one task's two horizons consistent for its lifetime,
        # while a new setting still applies to every creation submitted after it.
        "window_seconds": window_seconds,
    }
    if creation_sequence is not None:
        payload["creation_sequence"] = creation_sequence
    return payload


def enqueue_create_job(
    task_queue: "TaskQueueService",
    *,
    bot_id: str,
    entity_id: str,
    user_id: str,
    tenant: str,
    env: str,
    document_owner: str,
    spec: dict[str, Any],
    iframe_url: Optional[str],
    redirect_url: Optional[str],
    window_seconds: int,
    creation_sequence: Optional[str] = None,
) -> None:
    """Hand a submitted creation to the queue. Everything after this is the job's.

    Keyed, and this is the queue's **first** keyed call site — the mechanism
    shipped ahead of any adopter precisely so this could be reviewed on its own.
    The key buys one property: a submission retried by a caller who never saw the
    ``202`` cannot start a second job for the same bot. It does not make
    *submission* idempotent — a retry mints a fresh ``bot_id`` and so a fresh
    key, which is #1697's problem, not this one's.

    The deadline handed to the queue is deliberately **longer** than the
    authorization window — see ``CREATE_QUEUE_DEADLINE_MARGIN_SECONDS``. The
    window is the handler's, checked against ``submitted_at``, because a task
    retired in the claim scan never runs again and so would never delete the rows
    submission wrote. The queue's deadline is the backstop behind that.
    """
    # ``window_seconds`` is taken rather than read here, and that is what makes
    # the two horizons below consistent: the caller reads the setting once, so a
    # value that changes mid-flight cannot hand one task two deadlines computed
    # from different numbers.
    task_queue.enqueue(
        CREATE_JOB_TASK_TYPE,
        build_create_job_payload(
            bot_id=bot_id,
            entity_id=entity_id,
            user_id=user_id,
            tenant=tenant,
            env=env,
            document_owner=document_owner,
            spec=spec,
            iframe_url=iframe_url,
            redirect_url=redirect_url,
            window_seconds=window_seconds,
            creation_sequence=creation_sequence,
        ),
        window_seconds + CREATE_QUEUE_DEADLINE_MARGIN_SECONDS,
        idempotency_key=create_job_idempotency_key(
            tenant=tenant, entity_id=entity_id, bot_id=bot_id
        ),
    )


def find_create_job(
    task_queue: "TaskQueueService", *, tenant: str, entity_id: str, bot_id: str
) -> Optional[TaskRecord]:
    """This creation's task row, live or terminal, or ``None`` if there is none.

    ``None`` is a real answer and the poll depends on it twice over: no creation
    was ever submitted under this key — which is how a bot made by the ordinary
    endpoint is told apart from one whose creation failed, and equally how
    another owner's ``bot_id`` finds nothing rather than their pending creation.
    """
    return task_queue.find_by_idempotency_key(
        CREATE_JOB_TASK_TYPE,
        create_job_idempotency_key(
            tenant=tenant, entity_id=entity_id, bot_id=bot_id
        ),
    )


class BotCreateWithManifestHandler:
    """The step machine. Every step is a question about durable state."""

    def __init__(
        self,
        *,
        manifest_seam_provider: Callable[[], ManifestCreationSeam],
        apply_service_provider: Callable[[], BotConfigManifestApplyServiceProtocol],
        bot_repository_provider: Callable[[], BotRepository],
        passport_plugin_provider: Callable[[], PassportPlugin],
        # The three below stay ``Any``, and each for the same structural
        # reason rather than for want of a name: every one of them lives in
        # ``core/bot_management`` or the graph it pulls in, and this package is
        # what that graph reaches into. Naming their types here would close the
        # cycle at import time, which is why they arrive as callables the DI
        # module resolves. ``ManifestCreationSeam`` above is from that same
        # package and imported outright, which is not an exception: it is a
        # Protocol module that imports nothing but ``typing`` and one value
        # type, so it pulls none of the graph behind it. ``complete_authorization`` is
        # ``create_flow.complete_bot_authorization``, ``bot_service_provider``
        # yields ``BotService``, and ``auth_relationship_provider`` yields the
        # relationship writer the owner-repair step consults.
        complete_authorization: Callable[..., Any],
        bot_service_provider: Callable[[], Any],
        auth_relationship_provider: Callable[[], Any],
        creation_sequence: Optional[Callable[[Optional[str]], CreationSequence]] = None,
    ) -> None:
        self._seam_provider = manifest_seam_provider
        self._applies_provider = apply_service_provider
        self._bots_provider = bot_repository_provider
        self._complete_authorization = complete_authorization
        self._passport_provider = passport_plugin_provider
        self._bot_service_provider = bot_service_provider
        self._auth_relationship_provider = auth_relationship_provider
        # W8: which order this engine's creation runs in — the delivery
        # strategy's ``creation_sequence``. Absent (the pre-W8 wiring and the
        # suites built on it) means ``CREATE_BETWEEN_PHASES``, today's order.
        self._creation_sequence = creation_sequence or (
            lambda _engine: CreationSequence.CREATE_BETWEEN_PHASES
        )

    @property
    def task_type(self) -> str:
        return CREATE_JOB_TASK_TYPE

    def handle(self, payload: Optional[dict]) -> TaskOutcome:
        if not payload:
            logger.error("[manifest_create] empty payload, nothing to create")
            return Complete()
        # Re-established from the payload for every invocation. Getting this
        # wrong is silent — ``get_current_avernet_tenant`` returns the *default*
        # tenant outside a request rather than raising — so a dropped scope
        # would create the bot and read the manifest under the wrong tenant with
        # nothing raised anywhere.
        with avernet_tenant_scope(str(payload.get("tenant") or "")):
            return self._run(payload)

    def _run(self, payload: dict) -> TaskOutcome:
        bot_id = str(payload["bot_id"])
        entity_id = str(payload["entity_id"])

        bot = self._bots_provider().get_by_id_and_entity(bot_id, entity_id)
        if bot is None:
            return self._before_the_bot_exists(payload)
        if self._sequence(payload) is CreationSequence.RECORD_APPLY_PROVISION:
            return self._after_the_record_exists(payload, bot)
        return self._after_the_bot_exists(payload, bot)

    def _sequence(self, payload: dict) -> CreationSequence:
        # Frozen at submission (W8): the sequence the creation started under
        # is the one it finishes under, whatever the switch says now. A
        # payload from before the field existed asks the live strategy.
        frozen = payload.get("creation_sequence")
        if frozen:
            return CreationSequence(str(frozen))
        return self._creation_sequence(payload["spec"].get("engine_type"))

    # ── before there is a bot ───────────────────────────────────────────────

    def _before_the_bot_exists(self, payload: dict) -> TaskOutcome:
        bot_id = str(payload["bot_id"])
        entity_id = str(payload["entity_id"])
        user_id = str(payload["user_id"])

        if self._expired(payload):
            logger.info(
                "[manifest_create] bot_id=%s was never authorized within the "
                "window; discarding what submission wrote",
                bot_id,
            )
            if not self._discard(payload):
                return self._cleanup_did_not_land(bot_id)
            return Fail(AUTHORIZATION_WINDOW_ELAPSED)

        status = self._authorization_status(bot_id=bot_id, user_id=user_id)
        if status is None:
            # Passport has no answer yet — the apply is still propagating. A
            # wait, not a fault.
            return Reschedule(POLL_DELAY_SECONDS)
        if status == "PENDING":
            return Reschedule(POLL_DELAY_SECONDS)
        if status != "ISSUED":
            # Declined, expired, or anything else terminal the service reports.
            # Nothing was created, so the rows submission wrote are orphans that
            # only this can reach.
            logger.info(
                "[manifest_create] authorization ended as %s for bot_id=%s",
                status,
                bot_id,
            )
            if not self._discard(payload):
                return self._cleanup_did_not_land(bot_id)
            return Fail(f"authorization did not complete: {status}")

        if self._sequence(payload) is CreationSequence.RECORD_APPLY_PROVISION:
            # Authorized. Under this sequence the record comes first: the
            # single pre-container phase writes platform state against it,
            # and provisioning composes the first artifact from that state
            # (W8, plan K-6). Nothing is applied before the record exists.
            #
            # Deliberately not wrapped the way ``provision_bot`` is below: a
            # raising handler is the worker's implicit retry with backoff,
            # and a retry here is safe — the completion is idempotent on the
            # supplied ``bot_id``, so it cannot make a second bot, and the
            # next attempt finds the record if the first one did write it.
            # ``provision_bot`` is terminal only because the service
            # soft-deletes the record on failure, which a retry would then
            # re-create. Same shape as the ``CREATE_BETWEEN_PHASES`` call below.
            self._create_the_bot(payload, provision=False)
            return Reschedule(POLL_DELAY_SECONDS)

        # Authorized. The pre-container phase must land before creation, because
        # `_build_create_bot_payload` reads the startup-script row while
        # composing the start command.
        pre_container = self._pre_container_record(
            entity_id=entity_id, bot_id=bot_id
        )
        if pre_container is None:
            self._seam_provider().apply_pre_container(
                entity_id=entity_id,
                bot_id=bot_id,
                owner_id=user_id,
                actor_id=user_id,
                engine_type=payload["spec"].get("engine_type"),
                bot_type=payload["spec"].get("bot_type"),
            )
            return Reschedule(POLL_DELAY_SECONDS)
        if pre_container.status is ApplyStatus.RUNNING:
            return Reschedule(POLL_DELAY_SECONDS)

        # Terminal either way: a failed pre-container phase does not stop
        # creation (§2.7). The report says what did not land.
        self._create_the_bot(payload)
        return Reschedule(POLL_DELAY_SECONDS)

    # ── after there is a record (RECORD_APPLY_PROVISION, W8) ───────────────────

    def _after_the_record_exists(self, payload: dict, bot: dict) -> TaskOutcome:
        """The record exists; run the phase against it, then provision, then wait.

        Three questions about durable state, asked in order: is there a binding
        yet (no → the phase and provisioning are still ahead); is the phase's
        record terminal (no → wait; none → start it); is the container up. A
        failed phase still provisions (§2.7) — the report says what did not
        land. The post-container phase is never started: there is nothing
        left for it to deliver.
        """
        bot_id = str(payload["bot_id"])
        entity_id = str(payload["entity_id"])
        user_id = str(payload["user_id"])
        spec = payload["spec"]

        if not bot.get("binding_id"):
            pre_container = self._pre_container_record(
                entity_id=entity_id, bot_id=bot_id
            )
            if pre_container is None:
                self._seam_provider().apply_pre_container(
                    entity_id=entity_id,
                    bot_id=bot_id,
                    owner_id=user_id,
                    actor_id=user_id,
                    engine_type=spec.get("engine_type"),
                    bot_type=spec.get("bot_type"),
                    bot=bot,
                )
                return Reschedule(POLL_DELAY_SECONDS)
            if pre_container.status is ApplyStatus.RUNNING:
                return Reschedule(POLL_DELAY_SECONDS)
            # Terminal either way: provisioning composes the first artifact
            # from whatever the phase wrote. Idempotent on a bound record, so
            # a re-claimed task cannot provision twice.
            try:
                self._bot_service_provider().provision_bot(
                    bot_id,
                    user_id,
                    user_id,
                    template_type=spec.get("template_type"),
                    template_config=spec.get("template_config"),
                )
            except Exception as could_not_provision:  # noqa: BLE001 — terminal
                # The service soft-deletes the record on an allocation failure,
                # so a retry would create a second bot under the same id. The
                # rows submission and the phase wrote are orphans now — no
                # record for ordinary deletion to reach — so they go here.
                logger.exception(
                    "[manifest_create] bot_id=%s could not be provisioned", bot_id
                )
                if not self._discard(payload):
                    # Terminal regardless. A reschedule here would find the
                    # soft-deleted record absent, read the authorization as
                    # ISSUED and create the bot again under the same id; the
                    # rows fall to the sweeper instead.
                    logger.error(
                        "[manifest_create] bot_id=%s: provisioning failed and the "
                        "discard did not land; leaving the rows to the sweeper",
                        bot_id,
                    )
                return Fail(f"{BOT_COULD_NOT_BE_PROVISIONED}: {could_not_provision}")
            return Reschedule(POLL_DELAY_SECONDS)

        status = str(bot.get("status") or "")
        if status in _CONTAINER_FAILED_STATUSES:
            logger.warning(
                "[manifest_create] bot_id=%s reached %s; no container is coming",
                bot_id,
                status,
            )
            return Fail(f"{BOT_COULD_NOT_BE_PROVISIONED}: {status}")
        if status not in _CONTAINER_READY_STATUSES:
            return Reschedule(POLL_DELAY_SECONDS)
        self._repair_owner_relationship_if_missing(payload, bot)
        return Complete()

    def _discard(self, payload: dict) -> bool:
        """Remove what submission and the phase wrote; ``False`` if it did not land.

        The managed-files purge (``owner_id``) is asked for only under
        ``RECORD_APPLY_PROVISION``: the store is that sequence's, and a
        ``CREATE_BETWEEN_PHASES`` creation must not depend on a table it never wrote.
        """
        record_first = self._sequence(payload) is CreationSequence.RECORD_APPLY_PROVISION
        return self._seam_provider().discard(
            entity_id=str(payload["entity_id"]),
            bot_id=str(payload["bot_id"]),
            owner_id=str(payload["user_id"]) if record_first else None,
        )

    # ── after there is a bot ────────────────────────────────────────────────

    def _after_the_bot_exists(self, payload: dict, bot: dict) -> TaskOutcome:
        bot_id = str(payload["bot_id"])
        entity_id = str(payload["entity_id"])

        if self._on_container_record(entity_id=entity_id, bot_id=bot_id) is not None:
            # Already started. The job's work is done — it does not wait for the
            # post-container phase, because nothing depends on it.
            return Complete()

        status = str(bot.get("status") or "")
        if status in _CONTAINER_FAILED_STATUSES:
            logger.warning(
                "[manifest_create] bot_id=%s reached %s; no container is coming",
                bot_id,
                status,
            )
            return Fail(f"the bot could not be provisioned: {status}")
        if status not in _CONTAINER_READY_STATUSES:
            return Reschedule(POLL_DELAY_SECONDS)

        # The container is up and this is the job's last act, so it is also the
        # last chance to notice a completion that only half happened.
        self._repair_owner_relationship_if_missing(payload, bot)

        # Guarded, like its pre-container counterpart, and for a stronger reason
        # than symmetry: ``start_apply`` does real work *before* it enqueues
        # anything — it takes the apply lock and re-validates the stored document
        # against the bot's **current** engine. Two things can raise here, and
        # they want opposite answers:
        #
        #   * ``ManifestApplyInProgressError`` — somebody else holds the lock,
        #     most plausibly a manual ``POST …/config-manifest/apply`` racing the
        #     container becoming ACTIVE. That frees itself, so wait.
        #   * anything else — a document that no longer validates for this bot's
        #     engine, say — will not fix itself by being retried.
        #
        # Letting either propagate makes the worker retry until the queue's
        # deadline and retire the task ``TIMED_OUT``, which is the slowest
        # possible way to reach a worse answer.
        try:
            self._applies_provider().start_apply(
                entity_id=entity_id,
                bot_id=bot_id,
                bot=bot,
                owner_id=str(payload["user_id"]),
                actor_id=str(payload["user_id"]),
                trigger=CREATE_ON_CONTAINER_TRIGGER,
                phases=frozenset({ApplyPhase.ON_CONTAINER}),
                carry_from_apply_id=self._pre_container_apply_id(
                    entity_id=entity_id, bot_id=bot_id
                ),
            )
        except ManifestApplyInProgressError:
            logger.info(
                "[manifest_create] bot_id=%s has an apply in flight; waiting to "
                "start the post-container phase",
                bot_id,
            )
            return Reschedule(POLL_DELAY_SECONDS)
        except Exception as could_not_start:  # noqa: BLE001 — see above
            # Terminal, and deliberately so: the bot is up, so this is an
            # **apply** failure and the poll reports it as one — a terminal job
            # beside a running bot reads as `APPLY_FAILED`, carrying the bot.
            # Completing instead would report `READY` for a bot whose manifest
            # never ran, which is the one answer a caller must not be given.
            logger.exception(
                "[manifest_create] the post-container phase could not start for "
                "bot_id=%s; the bot is up and unconfigured",
                bot_id,
            )
            return Fail(
                f"the post-container phase could not start: {could_not_start}"
            )
        return Complete()

    # ── the questions each step asks ────────────────────────────────────────

    def _repair_owner_relationship_if_missing(self, payload: dict, bot: dict) -> None:
        """Write the owner→bot relationship if completion left it unwritten.

        **Why this exists.** ``complete_bot_authorization`` does two writes: it
        creates the bot record, then records the owner relationship. If the
        second raises, the task is re-claimed with a bot record already present
        — and routing on "does a bot exist" then carries the creation all the way
        to `READY` without ever retrying the write. The owner ends up holding a
        bot they cannot reach, and nothing anywhere says so.

        **Why here and not on every invocation.** This runs once, at the moment
        the job is about to declare itself done, rather than on each pass while
        the container comes up. One read per creation instead of one per poll.

        **Why it never blocks.** Nothing here returns an outcome: a repair that
        is needed is attempted and the creation continues either way. That is
        deliberate rather than lax — ``LocalAuthRelationship.query_relationships``
        answers ``[]`` unconditionally while its ``create_relationship`` reports
        success, so a read is not a reliable "it is missing" everywhere. Gating
        progress on it would stall every local and singlebox creation forever,
        trading a rare repairable gap for a certain outage. An unresolvable
        ``agent_code`` or a read that raises is likewise treated as "nothing to
        do".
        """
        from agentclaw.community.core.bot_management.utils import resolve_agent_code

        bot_id = str(payload["bot_id"])
        user_id = str(payload["user_id"])
        try:
            agent_code = resolve_agent_code(
                bot=bot,
                bot_id=bot_id,
                owner_id=user_id,
                passport_plugin=self._passport_provider(),
            )
            if not agent_code:
                return
            if self._auth_relationship_provider().query_relationships(
                agent_code=agent_code, work_no=user_id
            ):
                return
        except Exception:  # noqa: BLE001 — see docstring
            logger.exception(
                "[manifest_create] could not read the owner relationship for "
                "bot_id=%s; continuing rather than stalling the creation",
                bot_id,
            )
            return

        logger.warning(
            "[manifest_create] bot_id=%s has no owner relationship; re-running "
            "authorization completion to write it",
            bot_id,
        )
        try:
            # Idempotent on both halves: ``create_bot`` returns the existing bot
            # for a supplied id, and the relationship write is what we are here
            # for.
            self._create_the_bot(payload)
        except Exception:  # noqa: BLE001 — the bot is up; do not lose its config
            logger.exception(
                "[manifest_create] could not repair the owner relationship for "
                "bot_id=%s; the manifest is applied anyway",
                bot_id,
            )

    def _cleanup_did_not_land(self, bot_id: str) -> TaskOutcome:
        """A delete failed, so stay retryable instead of going terminal.

        Failing here would make the task terminal with the manifest or the
        startup-script row still present — and this job is the only thing that
        can ever reach them, because ordinary bot deletion needs a bot record
        that will never exist. A transient store failure would turn into a
        permanent orphan, which is exactly the class of row the deadline exists
        to bound and the reason this feature ships without a switch.

        Rescheduling instead is bounded rather than open-ended: the queue's own
        deadline still retires the task, so a store that stays broken gives up
        on its own rather than retrying forever. What is left then is #1698's
        general sweeper — the backstop, not the mechanism.
        """
        logger.warning(
            "[manifest_create] cleanup for bot_id=%s did not land; retrying "
            "rather than reporting the creation terminal with its rows intact",
            bot_id,
        )
        return Reschedule(POLL_DELAY_SECONDS)

    def _expired(self, payload: dict) -> bool:
        """Whether the authorization window has elapsed.

        Only asked **before the bot exists**: once creation has happened the
        window is irrelevant, and expiring then would delete the manifest of a
        bot that is running.
        """
        # The window this creation was enqueued under, not whatever the
        # environment says now — see ``window_seconds`` in the payload builder.
        # A payload written before that field existed falls back to the current
        # setting, which is what it was enqueued under anyway.
        window = payload.get("window_seconds") or DEFAULT_CREATE_DEADLINE_SECONDS
        submitted = payload.get("submitted_at")
        if not submitted:
            # A payload from before this field existed. Treat it as fresh rather
            # than instantly expired — the queue's own deadline still bounds it.
            return False
        try:
            started = datetime.fromisoformat(str(submitted))
        except ValueError:
            logger.warning(
                "[manifest_create] unreadable submitted_at=%r; leaving expiry "
                "to the queue's deadline",
                submitted,
            )
            return False
        return datetime.now() - started > timedelta(seconds=int(window))

    def _authorization_status(self, *, bot_id: str, user_id: str) -> Optional[str]:
        try:
            answer = self._passport_provider().query_auth_status(
                bot_id=bot_id, owner_workno=user_id
            )
        except Exception:  # noqa: BLE001 — a passport outage is a wait, not a verdict
            logger.exception(
                "[manifest_create] could not read authorization for bot_id=%s",
                bot_id,
            )
            return None
        if not answer:
            return None
        return str(answer.get("status") or "")

    def _pre_container_record(self, *, entity_id: str, bot_id: str):
        """The pre-container phase's record, recognised by its trigger.

        ``last_apply`` plus the trigger, which is the same read the poll makes —
        so no repository method is added for this.
        """
        return self._record_with_trigger(
            entity_id=entity_id,
            bot_id=bot_id,
            trigger=CREATE_PRE_CONTAINER_TRIGGER,
        )

    def _on_container_record(self, *, entity_id: str, bot_id: str):
        return self._record_with_trigger(
            entity_id=entity_id,
            bot_id=bot_id,
            trigger=CREATE_ON_CONTAINER_TRIGGER,
        )

    def _record_with_trigger(self, *, entity_id: str, bot_id: str, trigger: str):
        report = self._applies_provider().last_apply(
            entity_id=entity_id, bot_id=bot_id
        )
        if report is None or report.trigger != trigger:
            return None
        return report

    def _pre_container_apply_id(self, *, entity_id: str, bot_id: str) -> Optional[str]:
        record = self._pre_container_record(entity_id=entity_id, bot_id=bot_id)
        return record.apply_id if record is not None else None

    def _create_the_bot(self, payload: dict, *, provision: bool = True) -> None:
        """Complete the authorization, which is what writes the bot record.

        Reuses ``complete_bot_authorization``. It is idempotent on a supplied
        ``bot_id``, so a re-claimed task cannot make a second bot.
        ``provision=False`` (W8) records the bot and leaves provisioning to
        ``provision_bot``; the default call shape is unchanged.
        """
        if provision:
            self._complete_authorization(payload)
        else:
            self._complete_authorization(payload, provision=False)


class CreateJobLifecycle(LifecycleBase):
    """Registers the creation handler at boot.

    ``wake_on_enqueue=True``: a person is waiting at the other end of this, and
    the first step is a Passport read that may already be answerable.
    """

    def __init__(
        self,
        *,
        registry: HandlerRegistry,
        handler_provider: Callable[[], BotCreateWithManifestHandler],
    ) -> None:
        self._registry = registry
        self._handler_provider = handler_provider

    async def bootstrap(self) -> None:
        self._registry.register(self._handler_provider(), wake_on_enqueue=True)
        logger.info("[manifest_create] registered %s", CREATE_JOB_TASK_TYPE)


__all__ = [
    "AUTHORIZATION_WINDOW_ELAPSED",
    "CREATE_JOB_TASK_TYPE",
    "CREATE_QUEUE_DEADLINE_MARGIN_SECONDS",
    "DEFAULT_CREATE_DEADLINE_SECONDS",
    "POLL_DELAY_SECONDS",
    "BotCreateWithManifestHandler",
    "CreateJobLifecycle",
    "build_create_job_payload",
    "create_job_idempotency_key",
    "enqueue_create_job",
    "find_create_job",
]
