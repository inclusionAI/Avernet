"""Unit tests for the durable publish task handlers (Task 11)."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from agentclaw.community.core.service_bot.repository.models import PublishStatus
from agentclaw.community.core.task_queue.types import Complete, Fail, Reschedule
from agentclaw.community.core.service_bot.services.publish_flow.tasks import (
    PROGRESS_POLL_TASK,
    RESTART_POLL_TASK,
    PublishOnlineReleaseHandler,
    PublishProgressPollHandler,
    PublishRestartHandler,
    PublishRestartPollHandler,
    PublishVerifyFlowHandler,
)


class _FakeFlow:
    """Minimal PublishFlowService stand-in that tracks the record's status and
    records which stage methods ran."""

    def __init__(
        self,
        status: PublishStatus,
        *,
        missing=False,
        build_fails=False,
        verify_release_fails=False,
        online_release_fails=False,
        sync_to=None,
        online_recorded=False,
    ):
        self.status = status.value
        self.calls: list[str] = []
        self._missing = missing  # simulate a deleted/absent publish record
        self._build_fails = build_fails
        self._verify_release_fails = verify_release_fails
        self._online_release_fails = online_release_fails
        self._sync_to = sync_to  # status to move to when advance_publish_progress runs
        # Whether ext.publish.online is already recorded (the online-release
        # idempotency marker the online_release task guards on).
        self._online_recorded = online_recorded

    # Public facade accessors the durable task handlers use.
    def get_publish_record(self, publish_id):
        if self._missing:
            return None
        return SimpleNamespace(id=publish_id, status=self.status)

    def is_current_online_deployment(self, publish_id):
        return self._online_recorded

    async def execute_build_phase(self, record, operator):
        self.calls.append("build")
        if self._build_fails:
            self.status = PublishStatus.FAILED.value
            return SimpleNamespace(status=PublishStatus.FAILED, message="build boom")
        self.status = PublishStatus.BUILT.value
        return SimpleNamespace(status=PublishStatus.BUILT, message="Build completed")

    async def execute_verify_release_phase(self, record, operator):
        self.calls.append("verify_release")
        if self._verify_release_fails:
            self.status = PublishStatus.FAILED.value
            return SimpleNamespace(status=PublishStatus.FAILED, message="verify boom")
        self.status = PublishStatus.VALIDATE_PUB.value
        return SimpleNamespace(
            status=PublishStatus.VALIDATE_PUB, message="Released to the verify environment"
        )

    async def execute_release_phase(self, record, operator):
        self.calls.append("online_release")
        if self._online_release_fails:
            self.status = PublishStatus.FAILED.value
            return SimpleNamespace(status=PublishStatus.FAILED, message="online boom")
        self.status = PublishStatus.ONLINE_PUB.value
        self._online_recorded = True
        return SimpleNamespace(status=PublishStatus.ONLINE_PUB, message="Publish submitted")

    def advance_publish_progress(self, publish_id):
        self.calls.append("sync")
        if self._sync_to is not None:
            self.status = self._sync_to.value
        return SimpleNamespace(message="sync result")


def _handlers(flow):
    tq = Mock()
    return (
        PublishVerifyFlowHandler(flow=flow, task_queue_service=tq),
        PublishOnlineReleaseHandler(flow=flow, task_queue_service=tq),
        PublishProgressPollHandler(flow=flow, task_queue_service=tq, poll_delay_seconds=1.0),
        tq,
    )


# ── verify_flow ─────────────────────────────────────────────────────────────

def test_verify_flow_from_building_builds_releases_and_enqueues_poll():
    # process owns DRAFT -> BUILDING, so the task enters at BUILDING.
    flow = _FakeFlow(PublishStatus.BUILDING)
    verify, _online, _poll, tq = _handlers(flow)
    outcome = verify.handle({"publish_id": 1, "operator": "op"})
    assert isinstance(outcome, Complete)
    assert flow.calls == ["build", "verify_release"]
    assert flow.status == PublishStatus.VALIDATE_PUB.value
    tq.enqueue.assert_called_once()
    assert tq.enqueue.call_args.args[0] == PROGRESS_POLL_TASK


def test_verify_flow_build_failure_fails_task_without_release_or_poll():
    # The domain failure is already recorded on the publish record; the task
    # mirrors it as a terminal Fail (not a dishonest SUCCEEDED).
    flow = _FakeFlow(PublishStatus.BUILDING, build_fails=True)
    verify, _online, _poll, tq = _handlers(flow)
    outcome = verify.handle({"publish_id": 1, "operator": "op"})
    assert isinstance(outcome, Fail)
    assert "build failed" in outcome.error and "build boom" in outcome.error
    assert flow.calls == ["build"]
    tq.enqueue.assert_not_called()


def test_verify_flow_release_failure_fails_task_without_poll():
    flow = _FakeFlow(PublishStatus.BUILT, verify_release_fails=True)
    verify, _online, _poll, tq = _handlers(flow)
    outcome = verify.handle({"publish_id": 1, "operator": "op"})
    assert isinstance(outcome, Fail)
    assert "verify release failed" in outcome.error and "verify boom" in outcome.error
    assert flow.calls == ["verify_release"]
    tq.enqueue.assert_not_called()


def test_verify_flow_missing_record_fails_task():
    flow = _FakeFlow(PublishStatus.BUILDING, missing=True)
    verify, _online, _poll, tq = _handlers(flow)
    outcome = verify.handle({"publish_id": 1, "operator": "op"})
    assert isinstance(outcome, Fail)
    assert "not found" in outcome.error
    assert flow.calls == []
    tq.enqueue.assert_not_called()


def test_verify_flow_resumes_from_building_by_rebuilding():
    # Crash mid-build leaves BUILDING; a re-run simply rebuilds (no reset to DRAFT).
    flow = _FakeFlow(PublishStatus.BUILDING)
    verify, _online, _poll, tq = _handlers(flow)
    verify.handle({"publish_id": 1, "operator": "op"})
    assert flow.calls == ["build", "verify_release"]
    assert flow.status == PublishStatus.VALIDATE_PUB.value


def test_verify_flow_idempotent_from_validate_pub_only_enqueues_poll():
    flow = _FakeFlow(PublishStatus.VALIDATE_PUB)
    verify, _online, _poll, tq = _handlers(flow)
    verify.handle({"publish_id": 1, "operator": "op"})
    assert flow.calls == []  # no rebuild, no re-release
    tq.enqueue.assert_called_once()


def test_verify_flow_idempotent_from_validating_is_noop():
    flow = _FakeFlow(PublishStatus.VALIDATING)
    verify, _online, _poll, tq = _handlers(flow)
    verify.handle({"publish_id": 1, "operator": "op"})
    assert flow.calls == []
    tq.enqueue.assert_not_called()


def test_verify_flow_create_runs_once_across_reruns():
    """Crash-resume: once the release moved the record to VALIDATE_PUB, a re-run
    does not re-invoke the release (the create happens at most once here)."""
    flow = _FakeFlow(PublishStatus.BUILDING)
    verify, _online, _poll, tq = _handlers(flow)
    verify.handle({"publish_id": 1, "operator": "op"})
    verify.handle({"publish_id": 1, "operator": "op"})  # re-run at VALIDATE_PUB
    assert flow.calls.count("verify_release") == 1
    assert flow.calls.count("build") == 1


# ── online_release ──────────────────────────────────────────────────────────

def test_online_release_from_online_pub_releases_and_enqueues_poll():
    # process owns VALIDATING -> ONLINE_PUB, so the task enters at ONLINE_PUB and
    # runs the release within it.
    flow = _FakeFlow(PublishStatus.ONLINE_PUB)
    _verify, online, _poll, tq = _handlers(flow)
    online.handle({"publish_id": 1, "operator": "op"})
    assert flow.calls == ["online_release"]
    assert flow.status == PublishStatus.ONLINE_PUB.value
    tq.enqueue.assert_called_once()
    assert tq.enqueue.call_args.args[0] == PROGRESS_POLL_TASK


def test_online_release_idempotent_when_already_recorded_only_enqueues_poll():
    # Crash-resume: the release already recorded ext.publish.online → skip a second
    # create, just (re)enqueue the poll.
    flow = _FakeFlow(PublishStatus.ONLINE_PUB, online_recorded=True)
    _verify, online, _poll, tq = _handlers(flow)
    online.handle({"publish_id": 1, "operator": "op"})
    assert flow.calls == []  # no second online_release
    tq.enqueue.assert_called_once()
    assert tq.enqueue.call_args.args[0] == PROGRESS_POLL_TASK


def test_online_release_noop_from_validating_before_advance():
    # Defensive: an ONLINE_PUB-only handler does nothing if it somehow sees
    # VALIDATING (process advances before enqueuing, so this shouldn't happen).
    flow = _FakeFlow(PublishStatus.VALIDATING)
    _verify, online, _poll, tq = _handlers(flow)
    online.handle({"publish_id": 1, "operator": "op"})
    assert flow.calls == []
    tq.enqueue.assert_not_called()


def test_online_release_idempotent_from_success_is_noop():
    flow = _FakeFlow(PublishStatus.SUCCESS)
    _verify, online, _poll, tq = _handlers(flow)
    online.handle({"publish_id": 1, "operator": "op"})
    assert flow.calls == []
    tq.enqueue.assert_not_called()


def test_online_release_failure_fails_task_without_poll():
    flow = _FakeFlow(PublishStatus.ONLINE_PUB, online_release_fails=True)
    _verify, online, _poll, tq = _handlers(flow)
    outcome = online.handle({"publish_id": 1, "operator": "op"})
    assert isinstance(outcome, Fail)
    assert "online release failed" in outcome.error and "online boom" in outcome.error
    assert flow.calls == ["online_release"]
    tq.enqueue.assert_not_called()


def test_online_release_missing_record_fails_task():
    flow = _FakeFlow(PublishStatus.ONLINE_PUB, missing=True)
    _verify, online, _poll, tq = _handlers(flow)
    outcome = online.handle({"publish_id": 1, "operator": "op"})
    assert isinstance(outcome, Fail)
    assert "not found" in outcome.error
    assert flow.calls == []


# ── progress_poll ───────────────────────────────────────────────────────────

def test_poll_reschedules_while_still_in_validate_pub():
    flow = _FakeFlow(PublishStatus.VALIDATE_PUB, sync_to=PublishStatus.VALIDATE_PUB)
    _verify, _online, poll, _tq = _handlers(flow)
    outcome = poll.handle({"publish_id": 1})
    assert isinstance(outcome, Reschedule)
    assert flow.calls == ["sync"]


def test_poll_completes_when_baas_advances_to_validating():
    flow = _FakeFlow(PublishStatus.VALIDATE_PUB, sync_to=PublishStatus.VALIDATING)
    _verify, _online, poll, _tq = _handlers(flow)
    outcome = poll.handle({"publish_id": 1})
    assert isinstance(outcome, Complete)
    assert flow.calls == ["sync"]


def test_poll_noop_when_not_in_wait_state():
    flow = _FakeFlow(PublishStatus.SUCCESS)
    _verify, _online, poll, _tq = _handlers(flow)
    outcome = poll.handle({"publish_id": 1})
    assert isinstance(outcome, Complete)
    assert flow.calls == []  # sync not called


def test_poll_fails_task_when_baas_reports_failure():
    # BaaS FAILED → sync lands the record in FAILED → the poll task mirrors it.
    flow = _FakeFlow(PublishStatus.ONLINE_PUB, sync_to=PublishStatus.FAILED)
    _verify, _online, poll, _tq = _handlers(flow)
    outcome = poll.handle({"publish_id": 1})
    assert isinstance(outcome, Fail)
    assert "BaaS publish failed" in outcome.error
    assert flow.calls == ["sync"]


def test_poll_missing_record_fails_task():
    flow = _FakeFlow(PublishStatus.ONLINE_PUB, missing=True)
    _verify, _online, poll, _tq = _handlers(flow)
    outcome = poll.handle({"publish_id": 1})
    assert isinstance(outcome, Fail)
    assert "not found" in outcome.error
    assert flow.calls == []


def test_handler_invalid_payload_raises():
    flow = _FakeFlow(PublishStatus.DRAFT)
    verify, _online, _poll, _tq = _handlers(flow)
    with pytest.raises(ValueError):
        verify.handle({"operator": "op"})  # missing publish_id


# ── restart + restart_poll ──────────────────────────────────────────────────
# A restart runs on a stable record (VALIDATING / SUCCESS) and never transitions
# the publish status, so the status-keyed progress_poll cannot drive it — hence a
# separate task whose wait state is ext.restart.restarting.


class _FakeRestartFlow:
    """PublishFlowService stand-in for the restart pair: tracks the in-progress
    marker and the BaaS restart statuses ``sync_restart_progress`` reports."""

    def __init__(
        self,
        *,
        restart_succeeds=True,
        restarting=True,
        sync_statuses=None,
        missing=False,
    ):
        self.calls: list[str] = []
        self.restarting = restarting
        self._restart_succeeds = restart_succeeds
        self._missing = missing
        # Successive BaaS statuses returned by sync_restart_progress; the last one
        # repeats. ``None`` models the no-data early returns (unresolved workflow
        # id / progress-fetch error).
        self._sync_statuses = list(sync_statuses or [])

    def get_publish_record(self, publish_id):
        if self._missing:
            return None
        return SimpleNamespace(id=publish_id, status=PublishStatus.SUCCESS.value)

    async def execute_restart(self, *, publish_id, stage, operator):
        self.calls.append("execute_restart")
        if not self._restart_succeeds:
            return {"success": False, "message": "restart boom"}
        return {"success": True, "message": "Restart submitted", "stage": stage}

    def is_restart_in_progress(self, publish_id):
        return self.restarting

    def sync_restart_progress(self, publish_id):
        self.calls.append("sync_restart")
        status = self._sync_statuses.pop(0) if len(self._sync_statuses) > 1 else (
            self._sync_statuses[0] if self._sync_statuses else None
        )
        if status in ("SUCCESS", "FAILED"):
            self.restarting = False  # the sync clears the marker on terminal
        return SimpleNamespace(
            message=f"Restart publish status: {status}",
            data={"status": status} if status is not None else None,
        )


def _restart_handlers(flow):
    tq = Mock()
    return (
        PublishRestartHandler(flow=flow, task_queue_service=tq),
        PublishRestartPollHandler(flow=flow, task_queue_service=tq, poll_delay_seconds=1.0),
        tq,
    )


def test_restart_success_enqueues_restart_poll():
    flow = _FakeRestartFlow()
    restart, _poll, tq = _restart_handlers(flow)
    outcome = restart.handle({"publish_id": 1, "stage": "online", "operator": "op"})
    assert isinstance(outcome, Complete)
    tq.enqueue.assert_called_once()
    assert tq.enqueue.call_args.args[0] == RESTART_POLL_TASK
    assert tq.enqueue.call_args.args[1] == {"publish_id": 1}


def test_restart_failure_preserves_a_concurrent_restarts_marker():
    # Every ``success: False`` return in execute_restart is a preflight check that
    # runs BEFORE the marker is written, so a failing handler never set it. Since
    # restart_bot does not reject a restart while one is in flight, the marker it
    # would see belongs to a *concurrent* restart whose poll is still using it as
    # its wait state — clearing it would strand that restart.
    flow = _FakeRestartFlow(restart_succeeds=False, restarting=True)
    restart, _poll, tq = _restart_handlers(flow)
    outcome = restart.handle({"publish_id": 1, "stage": "online", "operator": "op"})
    assert isinstance(outcome, Fail)
    assert "restart failed" in outcome.error and "restart boom" in outcome.error
    assert flow.calls == ["execute_restart"]  # no clear_marker
    assert flow.restarting is True  # the other restart's poll still has its wait state
    tq.enqueue.assert_not_called()


def test_restart_failure_leaves_the_concurrent_poll_working():
    # End-to-end of the above: the failed handler must not turn the other
    # restart's poll into an immediate no-op.
    flow = _FakeRestartFlow(restart_succeeds=False, sync_statuses=["SUCCESS"])
    restart, poll, _tq = _restart_handlers(flow)
    restart.handle({"publish_id": 1, "stage": "online", "operator": "op"})
    outcome = poll.handle({"publish_id": 1})
    assert isinstance(outcome, Complete)
    assert flow.calls == ["execute_restart", "sync_restart"]  # sync still ran


def test_restart_poll_reschedules_while_baas_pending():
    flow = _FakeRestartFlow(sync_statuses=["PENDING"])
    _restart, poll, _tq = _restart_handlers(flow)
    outcome = poll.handle({"publish_id": 1})
    assert isinstance(outcome, Reschedule)
    assert outcome.delay_seconds == 1.0
    assert flow.calls == ["sync_restart"]


def test_restart_poll_completes_on_baas_success():
    # The SUCCESS observation is what activates a recreate's PENDING binding and
    # refreshes the provider MCP rule — it must happen without a /restart_status call.
    flow = _FakeRestartFlow(sync_statuses=["PENDING", "SUCCESS"])
    _restart, poll, _tq = _restart_handlers(flow)
    assert isinstance(poll.handle({"publish_id": 1}), Reschedule)
    assert isinstance(poll.handle({"publish_id": 1}), Complete)
    assert flow.calls == ["sync_restart", "sync_restart"]
    assert flow.restarting is False


def test_restart_poll_fails_task_on_baas_failure():
    flow = _FakeRestartFlow(sync_statuses=["FAILED"])
    _restart, poll, _tq = _restart_handlers(flow)
    outcome = poll.handle({"publish_id": 1})
    assert isinstance(outcome, Fail)
    assert "BaaS restart failed" in outcome.error


def test_restart_poll_completes_when_marker_already_cleared():
    # /restart_status (or an earlier run) already reconciled it — stop polling.
    flow = _FakeRestartFlow(restarting=False, sync_statuses=["PENDING"])
    _restart, poll, _tq = _restart_handlers(flow)
    outcome = poll.handle({"publish_id": 1})
    assert isinstance(outcome, Complete)
    assert flow.calls == []  # sync not called


def test_restart_poll_reschedules_when_sync_returns_no_data():
    # Unresolved workflow id / progress-fetch error: retry within the deadline
    # rather than declaring the restart done.
    flow = _FakeRestartFlow(sync_statuses=[None])
    _restart, poll, _tq = _restart_handlers(flow)
    outcome = poll.handle({"publish_id": 1})
    assert isinstance(outcome, Reschedule)
    assert flow.calls == ["sync_restart"]


def test_restart_poll_missing_record_fails_task():
    flow = _FakeRestartFlow(missing=True)
    _restart, poll, _tq = _restart_handlers(flow)
    outcome = poll.handle({"publish_id": 1})
    assert isinstance(outcome, Fail)
    assert "not found" in outcome.error
