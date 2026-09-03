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

import pytest

from agentclaw.community.core.bot_config_manifest.apply.outcomes import ApplyStatus
from agentclaw.community.core.bot_config_manifest.create_job import (
    BOT_COULD_NOT_BE_PROVISIONED,
    CREATE_JOB_TASK_TYPE,
    CREATE_QUEUE_DEADLINE_MARGIN_SECONDS,
    DEFAULT_CREATE_DEADLINE_SECONDS,
    POLL_DELAY_SECONDS,
    BotCreateWithManifestHandler,
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
        self.pre_container_calls = 0
        self.discards = 0
        self.discard_succeeds = discard_succeeds

    def apply_pre_container(self, **_kwargs):
        self.pre_container_calls += 1
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
    assert seam.pre_container_calls == 0 and not created


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
    assert seam.pre_container_calls == 1
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
    assert seam.pre_container_calls == 0 and not created

    # Issued, no phase A yet: the second invocation must not start a second one.
    handler, applies, seam, created = _handler(passport_status="ISSUED")
    handler.handle(dict(_PAYLOAD))
    handler.handle(dict(_PAYLOAD))
    assert seam.pre_container_calls == 1, "a re-claimed task started a second apply"

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


def test_the_window_comes_from_configuration_not_the_environment():
    """The window is the caller's to supply, and this module reads no settings.

    It used to come from ``os.environ`` here. Configuration belongs in
    ``user_config`` (``bot_create_with_manifest.authorization_window_seconds``),
    read once by the DI-constructed seam and handed down — so both horizons the
    enqueue computes come from a single reading, and a value that changes
    mid-flight cannot give one task two deadlines derived from different numbers.
    """
    from agentclaw.community.core.bot_config_manifest import create_job

    assert "os" not in vars(create_job), (
        "create_job must not reach for the environment; the window is a parameter"
    )

    queue = _RecordingQueue()
    _enqueue(queue, window_seconds=42)
    assert queue.calls[0]["payload"]["window_seconds"] == 42
    assert (
        queue.calls[0]["deadline_seconds"] == 42 + CREATE_QUEUE_DEADLINE_MARGIN_SECONDS
    )


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


def _enqueue(queue, *, window_seconds=DEFAULT_CREATE_DEADLINE_SECONDS):
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
        window_seconds=window_seconds,
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
    assert handed_to_the_queue > DEFAULT_CREATE_DEADLINE_SECONDS
    assert (
        handed_to_the_queue - DEFAULT_CREATE_DEADLINE_SECONDS
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


def test_the_window_is_the_one_frozen_at_enqueue_not_the_current_setting():
    """A setting changed mid-flight must not desynchronise a task's two horizons.

    The queue's deadline is computed from the window once, at enqueue. If the
    handler re-read the setting it could enforce a *longer* window than the
    deadline the database is holding — and the database would then retire the
    task before the handler ever ran its cleanup, which is precisely the race
    the margin exists to prevent.
    """
    handler, _applies, seam, _created = _handler(passport_status="PENDING")

    # Enqueued under a one-second window, and long past it.
    payload = dict(_PAYLOAD)
    payload["window_seconds"] = 1
    payload["submitted_at"] = "2020-01-01T00:00:00"

    assert isinstance(handler.handle(payload), Fail)
    assert seam.discards == 1


def test_a_payload_without_a_frozen_window_falls_back_rather_than_expiring():
    """A payload enqueued before the field existed is not instantly expired."""
    handler, _applies, seam, _created = _handler(passport_status="PENDING")

    payload = dict(_PAYLOAD)
    payload.pop("window_seconds", None)
    payload["submitted_at"] = datetime.now().isoformat()

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


# ── RECORD_APPLY_PROVISION: the record first, one phase, then provisioning (W8) ──

from agentclaw.community.core.bot_config_manifest.apply.delivery import (  # noqa: E402
    CreationSequence,
)

_TECLAW_PAYLOAD = {**_PAYLOAD, "spec": {"engine_type": "teclaw", "bot_type": "personal"}}


class _RecordingBotService:
    """``provision_bot`` as the job sees it: binds the record, or refuses."""

    def __init__(self, bots: "_Bots", *, refuse: bool = False) -> None:
        self._bots = bots
        self.refuse = refuse
        self.calls: list[dict] = []

    def provision_bot(self, bot_id, user_id, nick_name, **kw):
        self.calls.append({"bot_id": bot_id, "user_id": user_id, **kw})
        if self.refuse:
            raise RuntimeError("no capacity")
        self._bots.record = {**self._bots.record, "binding_id": 9, "status": "PENDING"}
        return self._bots.record


class _RecordingSeam(_Seam):
    def __init__(self, applies, **kw) -> None:
        super().__init__(applies, **kw)
        self.pre_container_kwargs: list[dict] = []
        self.discard_kwargs: list[dict] = []

    def apply_pre_container(self, **kwargs):
        self.pre_container_kwargs.append(kwargs)
        return super().apply_pre_container(**kwargs)

    def discard(self, **kwargs):
        self.discard_kwargs.append(kwargs)
        return super().discard(**kwargs)


def _record_first(
    *, passport_status="ISSUED", applies=None, bots=None, refuse=False, discard_succeeds=True,
    complete_raises=False,
):
    applies = applies or _Applies()
    seam = _RecordingSeam(applies, discard_succeeds=discard_succeeds)
    bots = bots or _Bots()
    created: list[tuple[dict, dict]] = []

    def complete(payload, **kw):
        created.append((payload, kw))
        if complete_raises:
            raise RuntimeError("the record could not be written")
        bots.record = {
            "bot_id": "b_1", "entity_id": "u_owner", "owner_id": "u_owner",
            "status": "PENDING", "binding_id": None, "active_engine": "teclaw",
            "ext": {"passport": {"agent_code": "agent-b1", "status": "ISSUED"}},
        }

    service = _RecordingBotService(bots, refuse=refuse)
    handler = BotCreateWithManifestHandler(
        manifest_seam_provider=lambda: seam,
        apply_service_provider=lambda: applies,
        bot_repository_provider=lambda: bots,
        complete_authorization=complete,
        passport_plugin_provider=lambda: _Passport(passport_status),
        bot_service_provider=lambda: service,
        auth_relationship_provider=lambda: _Relationships(),
        creation_sequence=lambda engine: (
            CreationSequence.RECORD_APPLY_PROVISION if engine == "teclaw" else CreationSequence.CREATE_BETWEEN_PHASES
        ),
    )
    return handler, applies, seam, bots, created, service


def test_record_first_walks_record_phase_provision_active_complete():
    handler, applies, seam, bots, created, service = _record_first()
    p = dict(_TECLAW_PAYLOAD)

    # 1. authorized → the record, unprovisioned; nothing applied yet.
    assert isinstance(handler.handle(p), Reschedule)
    assert created == [(p, {"provision": False})]
    assert seam.pre_container_calls == 0 and service.calls == []

    # 2. record, no binding, no phase → the phase runs against the real record.
    assert isinstance(handler.handle(p), Reschedule)
    assert seam.pre_container_calls == 1
    assert seam.pre_container_kwargs[0]["bot"] is bots.record
    assert service.calls == []

    # 3. phase running → wait; nothing provisioned.
    assert isinstance(handler.handle(p), Reschedule)
    assert service.calls == []

    # 4. phase terminal → provision, once.
    applies.latest = _Report(CREATE_PRE_CONTAINER_TRIGGER, ApplyStatus.SUCCEEDED)
    assert isinstance(handler.handle(p), Reschedule)
    assert [c["bot_id"] for c in service.calls] == ["b_1"]
    assert bots.record["binding_id"] == 9

    # 5. bound, container coming up → wait; bound and ACTIVE → done, and phase B
    #    is never started.
    assert isinstance(handler.handle(p), Reschedule)
    bots.record = {**bots.record, "status": "ACTIVE"}
    assert isinstance(handler.handle(p), Complete)
    assert applies.started == [], "no post-container phase under this sequence"
    assert len(created) == 1 and len(service.calls) == 1


def test_record_first_every_step_is_safe_to_run_twice():
    handler, applies, seam, bots, created, service = _record_first()
    p = dict(_TECLAW_PAYLOAD)
    handler.handle(p)
    handler.handle(p)  # record exists → phase starts once
    handler.handle(p)
    assert len(created) == 1 and seam.pre_container_calls == 1
    applies.latest = _Report(CREATE_PRE_CONTAINER_TRIGGER, ApplyStatus.SUCCEEDED)
    handler.handle(p)
    handler.handle(p)
    assert len(service.calls) == 1, "a re-claimed task provisioned twice"


def test_record_first_a_failed_phase_still_provisions():
    handler, applies, _seam, bots, _created, service = _record_first()
    p = dict(_TECLAW_PAYLOAD)
    handler.handle(p)
    applies.latest = _Report(CREATE_PRE_CONTAINER_TRIGGER, ApplyStatus.FAILED)
    handler.handle(p)
    assert len(service.calls) == 1


def test_record_first_provisioning_failure_is_terminal_and_discards():
    handler, applies, seam, _bots, _created, service = _record_first(refuse=True)
    p = dict(_TECLAW_PAYLOAD)
    handler.handle(p)
    applies.latest = _Report(CREATE_PRE_CONTAINER_TRIGGER, ApplyStatus.SUCCEEDED)
    outcome = handler.handle(p)
    assert isinstance(outcome, Fail) and outcome.error.startswith(BOT_COULD_NOT_BE_PROVISIONED)
    # The record is soft-deleted by the service: the rows the phase wrote are
    # orphans, and the job is the only thing that can reach them.
    assert seam.discard_kwargs == [{"entity_id": "u_owner", "bot_id": "b_1", "owner_id": "u_owner"}]


def test_create_between_phases_discards_without_touching_the_store():
    handler, _applies, seam, _bots, created, _service = _record_first(passport_status="REJECTED")
    assert isinstance(handler.handle(dict(_PAYLOAD)), Fail)  # claude_code → CREATE_BETWEEN_PHASES
    assert seam.discard_kwargs == [{"entity_id": "u_owner", "bot_id": "b_1", "owner_id": None}]
    assert not created


def test_record_first_a_declined_creation_purges_the_store_too():
    handler, _applies, seam, _bots, created, _service = _record_first(passport_status="REJECTED")
    assert isinstance(handler.handle(dict(_TECLAW_PAYLOAD)), Fail)
    assert seam.discard_kwargs == [{"entity_id": "u_owner", "bot_id": "b_1", "owner_id": "u_owner"}]
    assert not created


def test_create_between_phases_is_untouched_by_the_sequence_wiring():
    handler, applies, seam, _bots, created, service = _record_first()
    p = dict(_PAYLOAD)  # claude_code → CREATE_BETWEEN_PHASES
    handler.handle(p)
    assert seam.pre_container_calls == 1 and not created, "today's order: the phase first"
    applies.latest = _Report(CREATE_PRE_CONTAINER_TRIGGER, ApplyStatus.SUCCEEDED)
    handler.handle(p)
    assert created and created[0][1] == {}, "the default call shape, provisioning inline"
    assert service.calls == []


def test_record_first_a_failed_record_write_is_retried_not_discarded():
    """The record write is idempotent on ``bot_id``, so a raise here is the
    worker's implicit retry with backoff — not the terminal discard that
    ``provision_bot`` needs (the service soft-deletes the record there)."""
    handler, _applies, seam, _bots, created, service = _record_first(complete_raises=True)
    p = dict(_TECLAW_PAYLOAD)
    with pytest.raises(RuntimeError, match="could not be written"):
        handler.handle(p)
    assert len(created) == 1 and created[0][1] == {"provision": False}
    assert seam.discards == 0 and service.calls == []


def test_record_first_a_provisioning_failure_is_terminal_even_when_the_discard_does_not_land():
    """A reschedule here would re-create the soft-deleted bot under the same id."""
    handler, applies, seam, _bots, _created, _service = _record_first(refuse=True, discard_succeeds=False)
    p = dict(_TECLAW_PAYLOAD)
    handler.handle(p)
    applies.latest = _Report(CREATE_PRE_CONTAINER_TRIGGER, ApplyStatus.SUCCEEDED)
    outcome = handler.handle(p)
    assert isinstance(outcome, Fail) and outcome.error.startswith(BOT_COULD_NOT_BE_PROVISIONED)
    assert seam.discards == 1


def test_a_frozen_sequence_in_the_payload_wins_over_the_live_switch():
    """The sequence the creation started under is the one it finishes under."""
    handler, applies, seam, bots, created, service = _record_first()
    # A claude_code payload would route CREATE_BETWEEN_PHASES live; the frozen field says otherwise.
    p = {**_PAYLOAD, "creation_sequence": CreationSequence.RECORD_APPLY_PROVISION.value}
    handler.handle(p)
    assert created and created[0][1] == {"provision": False}
    assert seam.pre_container_calls == 0
    # And the other way round: a teclaw payload frozen as CREATE_BETWEEN_PHASES runs today's order.
    handler, applies, seam, bots, created, service = _record_first()
    p = {**_TECLAW_PAYLOAD, "creation_sequence": CreationSequence.CREATE_BETWEEN_PHASES.value}
    handler.handle(p)
    assert seam.pre_container_calls == 1 and not created


def test_the_payload_builder_freezes_the_sequence_only_when_given():
    from agentclaw.community.core.bot_config_manifest.create_job import build_create_job_payload

    base = dict(bot_id="b", entity_id="e", user_id="u", tenant="", env="dev", document_owner="u",
                spec={}, iframe_url=None, redirect_url=None, window_seconds=60)
    assert "creation_sequence" not in build_create_job_payload(**base)
    assert build_create_job_payload(**base, creation_sequence="record_apply_provision")["creation_sequence"] == "record_apply_provision"
