"""The creation job's step machine, and its re-entrancy.

Every step asks a question about durable state rather than tracking a cursor,
because the queue guarantees a single claimer but **at-least-once invocation**: a
crashed worker's task is re-claimed once its lease expires, whether or not a
handler ever asks for a retry. Each step is therefore driven twice here.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from agentclaw.community.core.bot_config_manifest.apply.outcomes import ApplyStatus
from agentclaw.community.core.bot_config_manifest.create_job import (
    CREATE_DEADLINE_ENV,
    CREATE_JOB_TASK_TYPE,
    CREATE_QUEUE_DEADLINE_MARGIN_SECONDS,
    POLL_DELAY_SECONDS,
    BotCreateWithManifestHandler,
    create_deadline_seconds,
    create_job_idempotency_key,
    enqueue_create_job,
)
from agentclaw.community.core.bot_config_manifest.bot_config_manifest_apply_service_protocol import (
    ManifestApplyInProgressError,
)
from agentclaw.community.core.bot_config_manifest.schema import (
    ManifestValidationError,
    Violation,
)
from agentclaw.community.core.bot_config_manifest.creation import (
    CREATE_ON_CONTAINER_TRIGGER,
    CREATE_PRE_CONTAINER_TRIGGER,
)
from agentclaw.community.core.task_queue.types import Complete, Fail, Reschedule

_PAYLOAD = {
    "bot_id": "b_1",
    "entity_id": "u_owner",
    "user_id": "u_owner",
    "tenant": "",
    "env": "dev",
    "document_owner": "u_owner",
    "spec": {"engine_type": "claude_code", "bot_type": "personal"},
    "iframe_url": "https://auth.example/consent",
    "redirect_url": None,
}


#: A bot as ``complete_bot_authorization`` leaves it: ACTIVE, with the
#: ``agent_code`` in ``ext`` that the owner-relationship lookup keys on.
_ISSUED_BOT = {
    "bot_id": "b_1",
    "entity_id": "u_owner",
    "status": "ACTIVE",
    "ext": {"passport": {"agent_code": "agent-b1", "status": "ISSUED"}},
}


@dataclass
class _Report:
    trigger: str
    status: ApplyStatus
    apply_id: str = "apply-a"


class _Applies:
    def __init__(self, latest: Optional[_Report] = None) -> None:
        self.latest = latest
        self.started: list[dict] = []

    def last_apply(self, *, entity_id, bot_id):
        return self.latest

    def start_apply(self, **kwargs):
        self.started.append(kwargs)
        self.latest = _Report(kwargs["trigger"], ApplyStatus.RUNNING, "apply-b")

        class _A:
            apply_id = "apply-b"

        return _A()


class _Seam:
    def __init__(self, applies: _Applies, *, discard_succeeds: bool = True) -> None:
        self._applies = applies
        self.phase_a_calls = 0
        self.discards = 0
        self.discard_succeeds = discard_succeeds

    def phase_a(self, **_kwargs):
        self.phase_a_calls += 1
        self._applies.latest = _Report(
            CREATE_PRE_CONTAINER_TRIGGER, ApplyStatus.RUNNING
        )
        return "apply-a"

    def discard(self, **_kwargs):
        self.discards += 1
        return self.discard_succeeds


class _Bots:
    def __init__(self, record=None) -> None:
        self.record = record

    def get_by_id_and_entity(self, bot_id, entity_id):
        return self.record


class _Relationships:
    """The owner→bot relationship, as much of it as the job reads.

    Defaults to "recorded", because that is the ordinary case: completion writes
    the relationship right after the bot record, and only a failure between the
    two leaves it absent.
    """

    def __init__(self, existing=None) -> None:
        self.existing = [{"auth_id": 1}] if existing is None else existing
        self.queries = 0

    def query_relationships(self, *, agent_code, work_no):
        self.queries += 1
        return self.existing


class _Passport:
    def __init__(self, status) -> None:
        self.status = status

    def query_auth_status(self, *, bot_id, owner_workno):
        return {"status": self.status} if self.status else None


def _handler(
    *, passport_status="PENDING", applies=None, bots=None, seam=None,
    relationships=None,
):
    applies = applies or _Applies()
    seam = seam or _Seam(applies)
    created: list[dict] = []
    relationships = relationships if relationships is not None else _Relationships()
    handler = BotCreateWithManifestHandler(
        manifest_seam_provider=lambda: seam,
        apply_service_provider=lambda: applies,
        bot_repository_provider=lambda: bots or _Bots(),
        complete_authorization=created.append,
        passport_plugin_provider=lambda: _Passport(passport_status),
        bot_service_provider=lambda: None,
        auth_relationship_provider=lambda: relationships,
    )
    return handler, applies, seam, created


def test_it_waits_while_authorization_is_pending():
    handler, _applies, seam, created = _handler(passport_status="PENDING")
    assert isinstance(handler.handle(dict(_PAYLOAD)), Reschedule)
    assert seam.phase_a_calls == 0 and not created


def test_a_passport_outage_is_a_wait_not_a_verdict():
    """No answer at all means keep asking; it must not look like a rejection."""
    handler, _applies, seam, _created = _handler(passport_status=None)
    assert isinstance(handler.handle(dict(_PAYLOAD)), Reschedule)
    assert seam.discards == 0, "an outage must not delete the caller's manifest"


def test_a_declined_authorization_is_terminal_and_cleans_up():
    handler, _applies, seam, created = _handler(passport_status="REJECTED")
    assert isinstance(handler.handle(dict(_PAYLOAD)), Fail)
    assert seam.discards == 1, (
        "nothing else can reach the rows submission wrote: no bot record was "
        "written, so ordinary deletion never gets to them"
    )
    assert not created


def test_the_pre_container_phase_runs_before_the_bot_is_created():
    handler, _applies, seam, created = _handler(passport_status="ISSUED")
    handler.handle(dict(_PAYLOAD))
    assert seam.phase_a_calls == 1
    assert not created, (
        "creation must not start until the startup-script row exists, or the "
        "first boot cannot carry the script"
    )


def test_it_creates_only_once_the_pre_container_phase_is_terminal():
    applies = _Applies(_Report(CREATE_PRE_CONTAINER_TRIGGER, ApplyStatus.RUNNING))
    handler, _applies, _seam, created = _handler(
        passport_status="ISSUED", applies=applies
    )
    assert isinstance(handler.handle(dict(_PAYLOAD)), Reschedule)
    assert not created

    applies.latest = _Report(CREATE_PRE_CONTAINER_TRIGGER, ApplyStatus.SUCCEEDED)
    handler.handle(dict(_PAYLOAD))
    assert len(created) == 1


def test_a_failed_pre_container_phase_still_creates_the_bot():
    """§2.7: a manifest-layer failure never prevents the bot."""
    applies = _Applies(_Report(CREATE_PRE_CONTAINER_TRIGGER, ApplyStatus.FAILED))
    handler, _applies, _seam, created = _handler(
        passport_status="ISSUED", applies=applies
    )
    handler.handle(dict(_PAYLOAD))
    assert len(created) == 1


def test_it_waits_for_the_container_then_starts_the_post_container_phase():
    applies = _Applies(_Report(CREATE_PRE_CONTAINER_TRIGGER, ApplyStatus.SUCCEEDED))
    bots = _Bots({"bot_id": "b_1", "status": "PENDING"})
    handler, _applies, _seam, _created = _handler(applies=applies, bots=bots)
    assert isinstance(handler.handle(dict(_PAYLOAD)), Reschedule)
    assert not applies.started

    bots.record = {"bot_id": "b_1", "status": "ACTIVE"}
    outcome = handler.handle(dict(_PAYLOAD))
    assert isinstance(outcome, Complete), (
        "the job does not wait for the post-container phase: nothing in the "
        "platform is blocked on it"
    )
    (started,) = applies.started
    assert started["trigger"] == CREATE_ON_CONTAINER_TRIGGER
    assert started["carry_from_apply_id"] == "apply-a", (
        "without the carry, the terminal report names the post-container "
        "categories and silently omits script"
    )


def test_a_bot_that_can_never_be_provisioned_is_terminal():
    applies = _Applies(_Report(CREATE_PRE_CONTAINER_TRIGGER, ApplyStatus.SUCCEEDED))
    bots = _Bots({"bot_id": "b_1", "status": "FAILED"})
    handler, *_ = _handler(applies=applies, bots=bots)
    assert isinstance(handler.handle(dict(_PAYLOAD)), Fail)


# ── re-entrancy: the queue is at-least-once, structurally ──────────────────


def test_every_step_is_safe_to_run_twice():
    # Pending: two invocations, still nothing done.
    handler, _applies, seam, created = _handler(passport_status="PENDING")
    handler.handle(dict(_PAYLOAD))
    handler.handle(dict(_PAYLOAD))
    assert seam.phase_a_calls == 0 and not created

    # Issued, no phase A yet: the second invocation must not start a second one.
    handler, applies, seam, created = _handler(passport_status="ISSUED")
    handler.handle(dict(_PAYLOAD))
    handler.handle(dict(_PAYLOAD))
    assert seam.phase_a_calls == 1, "a re-claimed task started a second apply"

    # Container up: the second invocation must not start a second phase B.
    applies = _Applies(_Report(CREATE_PRE_CONTAINER_TRIGGER, ApplyStatus.SUCCEEDED))
    bots = _Bots({"bot_id": "b_1", "status": "ACTIVE"})
    handler, applies, _seam, _created = _handler(applies=applies, bots=bots)
    handler.handle(dict(_PAYLOAD))
    handler.handle(dict(_PAYLOAD))
    assert len(applies.started) == 1, "a re-claimed task started a second apply"


# ── the deadline, and what happens at it ───────────────────────────────────


def test_an_unauthorized_creation_expires_and_takes_its_rows_with_it(monkeypatch):
    """The handler owns expiry, not the queue.

    The queue retires a task **DB-side in its claim scan**, so the handler never
    runs again — nothing would delete the manifest and startup-script rows
    submission wrote, and nothing else can reach them: no bot record exists, so
    ordinary deletion never gets to them, and allocating a bot_id consumes no
    quota, so the tenant ceiling does not bound them either.
    """
    from datetime import datetime, timedelta

    handler, _applies, seam, created = _handler(passport_status="PENDING")
    payload = dict(_PAYLOAD)
    payload["submitted_at"] = (
        datetime.now() - timedelta(seconds=601)
    ).isoformat()

    outcome = handler.handle(payload)

    assert isinstance(outcome, Fail)
    assert seam.discards == 1, "an abandoned creation must leave no rows behind"
    assert not created


def test_a_creation_inside_the_window_keeps_waiting():
    from datetime import datetime, timedelta

    handler, _applies, seam, _created = _handler(passport_status="PENDING")
    payload = dict(_PAYLOAD)
    payload["submitted_at"] = (datetime.now() - timedelta(seconds=60)).isoformat()

    assert isinstance(handler.handle(payload), Reschedule)
    assert seam.discards == 0


def test_expiry_is_not_checked_once_the_bot_exists():
    """Expiring then would delete the manifest of a bot that is running."""
    from datetime import datetime, timedelta

    applies = _Applies(_Report(CREATE_PRE_CONTAINER_TRIGGER, ApplyStatus.SUCCEEDED))
    bots = _Bots({"bot_id": "b_1", "status": "ACTIVE"})
    handler, applies, seam, _created = _handler(applies=applies, bots=bots)
    payload = dict(_PAYLOAD)
    payload["submitted_at"] = (
        datetime.now() - timedelta(seconds=99999)
    ).isoformat()

    assert isinstance(handler.handle(payload), Complete)
    assert seam.discards == 0
    assert applies.started, "the post-container phase must still start"


def test_the_deadline_is_configurable(monkeypatch):
    from agentclaw.community.core.bot_config_manifest.create_job import (
        CREATE_DEADLINE_ENV,
        create_deadline_seconds,
    )

    monkeypatch.setenv(CREATE_DEADLINE_ENV, "42")
    assert create_deadline_seconds() == 42
    # A value that would expire everything immediately is refused, not obeyed.
    monkeypatch.setenv(CREATE_DEADLINE_ENV, "0")
    assert create_deadline_seconds() == 600
    monkeypatch.setenv(CREATE_DEADLINE_ENV, "not-a-number")
    assert create_deadline_seconds() == 600


class _RecordingQueue:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def enqueue(self, task_type, payload, deadline_seconds, **kwargs):
        self.calls.append(
            {
                "task_type": task_type,
                "payload": payload,
                "deadline_seconds": deadline_seconds,
                **kwargs,
            }
        )
        return (None, True)


def _enqueue(queue):
    enqueue_create_job(
        queue,
        bot_id="b_1",
        entity_id="u_owner",
        user_id="u_owner",
        tenant="t1",
        env="dev",
        document_owner="u_owner",
        spec={"engine_type": "claude_code", "bot_type": "personal"},
        iframe_url=None,
        redirect_url=None,
    )


def test_the_queues_deadline_is_longer_than_the_authorization_window():
    """The handler has to reach its own expiry check first, and this is why.

    A past-deadline task is retired **in the claim scan**, DB-side, without the
    handler ever running — and the handler is the only thing that deletes the
    manifest and startup-script rows submission wrote. With both horizons equal
    the queue always won that race, so the cleanup was unreachable and an
    abandoned creation left its rows behind: exactly the orphan class the
    deadline exists to bound. Caught by the endpoint case for expiry, which
    could not reach a terminal outcome at all.

    The margin also has to leave room for the job to *notice*: it looks again
    every ``POLL_DELAY_SECONDS``, so anything smaller than that would reopen the
    same race from the other side.
    """
    queue = _RecordingQueue()
    _enqueue(queue)

    handed_to_the_queue = queue.calls[0]["deadline_seconds"]
    assert handed_to_the_queue > create_deadline_seconds()
    assert (
        handed_to_the_queue - create_deadline_seconds()
        == CREATE_QUEUE_DEADLINE_MARGIN_SECONDS
    )
    assert CREATE_QUEUE_DEADLINE_MARGIN_SECONDS > POLL_DELAY_SECONDS


def test_the_creation_is_enqueued_under_a_key_scoped_the_way_its_rows_are():
    """Same three parts as the manifest's own storage key, and for the reason.

    The queue's dedup scope is ``(env, app, task_type)`` and knows nothing about
    tenants or owners, so anything left out of the key is something the poll's
    lookup does not scope by. Without ``entity_id`` another owner's ``bot_id``
    would find their pending creation — and its authorization URL.
    """
    queue = _RecordingQueue()
    _enqueue(queue)

    key = queue.calls[0]["idempotency_key"]
    assert key == create_job_idempotency_key(
        tenant="t1", entity_id="u_owner", bot_id="b_1"
    )
    assert "t1" in key and "u_owner" in key and "b_1" in key
    assert queue.calls[0]["task_type"] == CREATE_JOB_TASK_TYPE


def test_a_cleanup_that_did_not_land_keeps_the_task_retryable():
    """Going terminal here would strand the rows this job alone can reach.

    Ordinary bot deletion needs a bot record, and this creation will never have
    one, so a manifest or startup-script row left behind by a failed delete is
    permanent. Rescheduling retries it; the queue's own deadline still bounds
    how long, so a store that stays broken gives up rather than spinning.
    """
    applies = _Applies()
    seam = _Seam(applies, discard_succeeds=False)
    handler, _applies, _seam, created = _handler(
        passport_status="REJECTED", applies=applies, seam=seam
    )

    outcome = handler.handle(dict(_PAYLOAD))

    assert isinstance(outcome, Reschedule), outcome
    assert seam.discards == 1
    assert not created

    # And once the store recovers, the same step goes terminal as it should.
    seam.discard_succeeds = True
    assert isinstance(handler.handle(dict(_PAYLOAD)), Fail)


def test_the_window_is_the_one_frozen_at_enqueue_not_the_current_setting(
    monkeypatch,
):
    """A setting changed mid-flight must not desynchronise a task's two horizons.

    The queue's deadline is computed from the window once, at enqueue. If the
    handler re-read the environment it could enforce a *longer* window than the
    deadline the database is holding — and the database would then retire the
    task before the handler ever ran its cleanup, which is precisely the race
    the margin exists to prevent.
    """
    handler, _applies, seam, _created = _handler(passport_status="PENDING")

    # Enqueued under a one-second window, and long past it.
    payload = dict(_PAYLOAD)
    payload["window_seconds"] = 1
    payload["submitted_at"] = "2020-01-01T00:00:00"

    # The environment now says something far larger. It must not apply here.
    monkeypatch.setenv(CREATE_DEADLINE_ENV, "86400")

    assert isinstance(handler.handle(payload), Fail)
    assert seam.discards == 1


def test_a_payload_without_a_frozen_window_falls_back_rather_than_expiring(
    monkeypatch,
):
    """A payload enqueued before the field existed is not instantly expired."""
    handler, _applies, seam, _created = _handler(passport_status="PENDING")

    payload = dict(_PAYLOAD)
    payload.pop("window_seconds", None)
    payload["submitted_at"] = datetime.now().isoformat()
    monkeypatch.setenv(CREATE_DEADLINE_ENV, "600")

    assert isinstance(handler.handle(payload), Reschedule)
    assert seam.discards == 0


def test_a_bot_whose_owner_relationship_never_landed_gets_it_retried():
    """Completion is two writes, and the bot record is only the first.

    ``complete_bot_authorization`` creates the bot and *then* records the owner
    relationship. If the second raises, the task is re-claimed with a bot record
    already present — and routing on "does a bot exist" would carry the creation
    to READY without ever retrying the write, leaving the owner holding a bot
    they cannot reach and nothing anywhere saying so.
    """
    bots = _Bots(_ISSUED_BOT)
    missing = _Relationships(existing=[])
    handler, applies, _seam, created = _handler(
        passport_status="ISSUED", bots=bots, relationships=missing
    )

    outcome = handler.handle(dict(_PAYLOAD))

    assert isinstance(outcome, Complete), outcome
    assert created, "completion was not re-run to write the relationship"
    # The repair happens *before* the post-container phase is started, which is
    # the job's last act — so the creation is never declared done with the owner
    # unable to reach their bot.
    assert applies.started, "the manifest was not delivered"
    assert missing.queries == 1, (
        "the read must happen once per creation, not once per poll of a bot "
        "waiting for its container"
    )


def test_an_unreadable_relationship_does_not_stall_a_working_creation():
    """Only a definite absence is acted on.

    A lookup that fails says nothing about whether the row is there, and the bot
    is up: blocking delivery of its configuration behind a flaky read would
    trade a rare, repairable gap for a common one.
    """

    class _Unreadable:
        def query_relationships(self, *, agent_code, work_no):
            raise RuntimeError("the relationship service is unreachable")

    bots = _Bots(_ISSUED_BOT)
    handler, applies, _seam, created = _handler(
        passport_status="ISSUED", bots=bots, relationships=_Unreadable()
    )

    assert isinstance(handler.handle(dict(_PAYLOAD)), Complete)
    assert applies.started, "the post-container phase never started"
    assert not created, "completion was re-run on an answer that was not an answer"


def test_a_relationship_read_that_always_says_missing_does_not_stall():
    """The local plugin answers ``[]`` unconditionally while its write reports
    success, so "missing" is not a reliable fact everywhere.

    Gating the creation on it would stall every local and singlebox creation
    forever — trading a rare, repairable gap for a certain outage. The repair is
    attempted and the creation continues either way.
    """
    bots = _Bots(_ISSUED_BOT)
    always_missing = _Relationships(existing=[])
    handler, applies, _seam, _created = _handler(
        passport_status="ISSUED", bots=bots, relationships=always_missing
    )

    assert isinstance(handler.handle(dict(_PAYLOAD)), Complete)
    assert applies.started, "a stubbed read stopped the manifest being delivered"


def test_a_post_container_phase_that_cannot_start_is_an_apply_failure():
    """The bot is up, so this is an apply failure — never a create failure.

    ``start_apply`` does real work before it enqueues anything: it takes the
    lock and re-validates the stored document against the bot's *current*
    engine. Letting a failure there propagate would make the worker retry until
    the queue's deadline and retire the task, and the poll would then report a
    running bot as `CREATE_FAILED` — the "did I get a bot or not?" ambiguity the
    two terminal states exist to remove. Failing here instead reaches the same
    terminal row immediately, and the poll reads it as `APPLY_FAILED`.
    """

    class _RefusingApplies(_Applies):
        def start_apply(self, **kwargs):
            raise ManifestValidationError(
                [Violation(location="manifest.mcp", code="nope", message="no")]
            )

    applies = _RefusingApplies(
        _Report(CREATE_PRE_CONTAINER_TRIGGER, ApplyStatus.SUCCEEDED)
    )
    handler, _applies, _seam, _created = _handler(
        passport_status="ISSUED", bots=_Bots(_ISSUED_BOT), applies=applies
    )

    outcome = handler.handle(dict(_PAYLOAD))

    assert isinstance(outcome, Fail), outcome
    assert "post-container phase could not start" in outcome.error


def test_an_apply_already_in_flight_is_waited_out_not_failed():
    """A manual apply racing the container becoming ACTIVE frees itself.

    Reporting the creation failed because somebody else briefly held the lock
    would turn a few seconds of contention into a permanently unconfigured bot.
    """

    class _LockedApplies(_Applies):
        def start_apply(self, **kwargs):
            raise ManifestApplyInProgressError("someone else is applying")

    applies = _LockedApplies(
        _Report(CREATE_PRE_CONTAINER_TRIGGER, ApplyStatus.SUCCEEDED)
    )
    handler, _applies, _seam, _created = _handler(
        passport_status="ISSUED", bots=_Bots(_ISSUED_BOT), applies=applies
    )

    assert isinstance(handler.handle(dict(_PAYLOAD)), Reschedule)
