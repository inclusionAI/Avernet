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

import os
from datetime import datetime, timedelta
from typing import Any, Callable, Optional

from agentclaw.community.core.bot_config_manifest.apply.order import ApplyPhase
from agentclaw.community.core.bot_config_manifest.apply.outcomes import ApplyStatus
from agentclaw.community.core.bot_config_manifest.creation import (
    CREATE_ON_CONTAINER_TRIGGER,
    CREATE_PRE_CONTAINER_TRIGGER,
)
from agentclaw.community.core.task_queue.services.registry import HandlerRegistry
from agentclaw.community.core.task_queue.types import (
    Complete,
    Fail,
    Reschedule,
    TaskOutcome,
)
from agentclaw.community.kernel.lifecycle import LifecycleBase
from agentclaw.community.log import get_logger
from agentclaw.community.utils.avernet_tenant import avernet_tenant_scope

logger = get_logger()

#: The registry key for a creation.
CREATE_JOB_TASK_TYPE = "config_manifest.create_bot"

#: How long a user has to follow the authorization link before the creation is
#: retired. Configurable; this is the default the design settled on.
DEFAULT_CREATE_DEADLINE_SECONDS = 10 * 60

#: Environment override for the above.
CREATE_DEADLINE_ENV = "BOT_CREATE_WITH_MANIFEST_DEADLINE_SECONDS"


def create_deadline_seconds() -> int:
    """The window a user has to authorize, in seconds.

    Read per call rather than at import so a deployment can change it without a
    rebuild, and clamped to something positive: a zero or negative deadline would
    expire every creation the instant it was submitted.
    """
    raw = os.environ.get(CREATE_DEADLINE_ENV)
    if not raw:
        return DEFAULT_CREATE_DEADLINE_SECONDS
    try:
        parsed = int(raw)
    except ValueError:
        logger.warning(
            "[manifest_create] %s=%r is not an integer; using %ss",
            CREATE_DEADLINE_ENV,
            raw,
            DEFAULT_CREATE_DEADLINE_SECONDS,
        )
        return DEFAULT_CREATE_DEADLINE_SECONDS
    if parsed <= 0:
        logger.warning(
            "[manifest_create] %s=%s would expire every creation immediately; "
            "using %ss",
            CREATE_DEADLINE_ENV,
            parsed,
            DEFAULT_CREATE_DEADLINE_SECONDS,
        )
        return DEFAULT_CREATE_DEADLINE_SECONDS
    return parsed

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
    submitted_at: Optional[str] = None,
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
    """
    return {
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
    }


class BotCreateWithManifestHandler:
    """The step machine. Every step is a question about durable state."""

    def __init__(
        self,
        *,
        manifest_seam_provider: Callable[[], Any],
        apply_service_provider: Callable[[], Any],
        bot_repository_provider: Callable[[], Any],
        complete_authorization: Callable[..., Any],
        passport_plugin_provider: Callable[[], Any],
        bot_service_provider: Callable[[], Any],
    ) -> None:
        self._seam_provider = manifest_seam_provider
        self._applies_provider = apply_service_provider
        self._bots_provider = bot_repository_provider
        self._complete_authorization = complete_authorization
        self._passport_provider = passport_plugin_provider
        self._bot_service_provider = bot_service_provider

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
        user_id = str(payload["user_id"])

        bot = self._bots_provider().get_by_id_and_entity(bot_id, entity_id)
        if bot is None:
            return self._before_the_bot_exists(payload)
        return self._after_the_bot_exists(payload, bot)

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
            self._seam_provider().discard(entity_id=entity_id, bot_id=bot_id)
            return Fail("the authorization window elapsed")

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
            self._seam_provider().discard(entity_id=entity_id, bot_id=bot_id)
            return Fail(f"authorization did not complete: {status}")

        # Authorized. The pre-container phase must land before creation, because
        # `_build_create_bot_payload` reads the startup-script row while
        # composing the start command.
        phase_a = self._phase_a_record(entity_id=entity_id, bot_id=bot_id)
        if phase_a is None:
            self._seam_provider().phase_a(
                entity_id=entity_id,
                bot_id=bot_id,
                owner_id=user_id,
                actor_id=user_id,
                engine_type=payload["spec"].get("engine_type"),
                bot_type=payload["spec"].get("bot_type"),
            )
            return Reschedule(POLL_DELAY_SECONDS)
        if phase_a.status is ApplyStatus.RUNNING:
            return Reschedule(POLL_DELAY_SECONDS)

        # Terminal either way: a failed pre-container phase does not stop
        # creation (§2.7). The report says what did not land.
        self._create_the_bot(payload)
        return Reschedule(POLL_DELAY_SECONDS)

    # ── after there is a bot ────────────────────────────────────────────────

    def _after_the_bot_exists(self, payload: dict, bot: dict) -> TaskOutcome:
        bot_id = str(payload["bot_id"])
        entity_id = str(payload["entity_id"])

        if self._phase_b_record(entity_id=entity_id, bot_id=bot_id) is not None:
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

        self._applies_provider().start_apply(
            entity_id=entity_id,
            bot_id=bot_id,
            bot=bot,
            owner_id=str(payload["user_id"]),
            actor_id=str(payload["user_id"]),
            trigger=CREATE_ON_CONTAINER_TRIGGER,
            phases=frozenset({ApplyPhase.ON_CONTAINER}),
            carry_from_apply_id=self._phase_a_apply_id(
                entity_id=entity_id, bot_id=bot_id
            ),
        )
        return Complete()

    # ── the questions each step asks ────────────────────────────────────────

    def _expired(self, payload: dict) -> bool:
        """Whether the authorization window has elapsed.

        Only asked **before the bot exists**: once creation has happened the
        window is irrelevant, and expiring then would delete the manifest of a
        bot that is running.
        """
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
        return datetime.now() - started > timedelta(seconds=create_deadline_seconds())

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

    def _phase_a_record(self, *, entity_id: str, bot_id: str):
        """The pre-container phase's record, recognised by its trigger.

        ``last_apply`` plus the trigger, which is the same read the poll makes —
        so no repository method is added for this.
        """
        return self._record_with_trigger(
            entity_id=entity_id,
            bot_id=bot_id,
            trigger=CREATE_PRE_CONTAINER_TRIGGER,
        )

    def _phase_b_record(self, *, entity_id: str, bot_id: str):
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

    def _phase_a_apply_id(self, *, entity_id: str, bot_id: str) -> Optional[str]:
        record = self._phase_a_record(entity_id=entity_id, bot_id=bot_id)
        return record.apply_id if record is not None else None

    def _create_the_bot(self, payload: dict) -> None:
        """Complete the authorization, which is what writes the bot record.

        Reuses ``complete_bot_authorization`` unmodified. It is idempotent on a
        supplied ``bot_id``, so a re-claimed task cannot make a second bot.
        """
        self._complete_authorization(payload)


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
    "CREATE_DEADLINE_ENV",
    "CREATE_JOB_TASK_TYPE",
    "DEFAULT_CREATE_DEADLINE_SECONDS",
    "POLL_DELAY_SECONDS",
    "BotCreateWithManifestHandler",
    "CreateJobLifecycle",
    "build_create_job_payload",
    "create_deadline_seconds",
]
