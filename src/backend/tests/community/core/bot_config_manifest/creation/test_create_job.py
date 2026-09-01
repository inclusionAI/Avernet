"""The creation job's step machine, and its re-entrancy.

Every step asks a question about durable state rather than tracking a cursor,
because the queue guarantees a single claimer but **at-least-once invocation**: a
crashed worker's task is re-claimed once its lease expires, whether or not a
handler ever asks for a retry. Each step is therefore driven twice here.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from agentclaw.community.core.bot_config_manifest.apply.outcomes import ApplyStatus
from agentclaw.community.core.bot_config_manifest.create_job import (
    CREATE_JOB_TASK_TYPE,
    CREATE_QUEUE_DEADLINE_MARGIN_SECONDS,
    POLL_DELAY_SECONDS,
    BotCreateWithManifestHandler,
    create_deadline_seconds,
    create_job_idempotency_key,
    enqueue_create_job,
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
    def __init__(self, applies: _Applies) -> None:
        self._applies = applies
        self.phase_a_calls = 0
        self.discards = 0

    def phase_a(self, **_kwargs):
        self.phase_a_calls += 1
        self._applies.latest = _Report(
            CREATE_PRE_CONTAINER_TRIGGER, ApplyStatus.RUNNING
        )
        return "apply-a"

    def discard(self, **_kwargs):
        self.discards += 1


class _Bots:
    def __init__(self, record=None) -> None:
        self.record = record

    def get_by_id_and_entity(self, bot_id, entity_id):
        return self.record


class _Passport:
    def __init__(self, status) -> None:
        self.status = status

    def query_auth_status(self, *, bot_id, owner_workno):
        return {"status": self.status} if self.status else None


def _handler(*, passport_status="PENDING", applies=None, bots=None, seam=None):
    applies = applies or _Applies()
    seam = seam or _Seam(applies)
    created: list[dict] = []
    handler = BotCreateWithManifestHandler(
        manifest_seam_provider=lambda: seam,
        apply_service_provider=lambda: applies,
        bot_repository_provider=lambda: bots or _Bots(),
        complete_authorization=created.append,
        passport_plugin_provider=lambda: _Passport(passport_status),
        bot_service_provider=lambda: None,
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
