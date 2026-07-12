"""Unit tests for the durable publish task handlers (Task 11)."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from agentclaw.community.core.service_bot.repository.models import PublishStatus
from agentclaw.community.core.task_queue.types import Complete, Reschedule
from agentclaw.community.core.service_bot.services.publish_flow.tasks import (
    PROGRESS_POLL_TASK,
    PublishOnlineReleaseHandler,
    PublishProgressPollHandler,
    PublishVerifyFlowHandler,
)


class _FakeFlow:
    """Minimal PublishFlowService stand-in that tracks the record's status and
    records which stage methods ran."""

    def __init__(self, status: PublishStatus, *, build_fails=False, sync_to=None):
        self.status = status.value
        self.calls: list[str] = []
        self._build_fails = build_fails
        self._sync_to = sync_to  # status to move to when sync_publish_progress runs
        self._publish_service = Mock()
        self._publish_service.get_publish_by_id.side_effect = (
            lambda pid: SimpleNamespace(id=pid, status=self.status)
        )
        self._publish_service.update_publish_status.side_effect = (
            lambda pid, target, source: setattr(self, "status", target)
        )

    async def _execute_build_phase(self, record, operator):
        self.calls.append("build")
        if self._build_fails:
            self.status = PublishStatus.FAILED.value
            return SimpleNamespace(status=PublishStatus.FAILED)
        self.status = PublishStatus.BUILT.value
        return SimpleNamespace(status=PublishStatus.BUILT)

    async def _execute_verify_release_phase(self, record, operator):
        self.calls.append("verify_release")
        self.status = PublishStatus.VALIDATE_PUB.value
        return SimpleNamespace()

    async def _execute_release_phase(self, record, operator):
        self.calls.append("online_release")
        self.status = PublishStatus.ONLINE_PUB.value
        return SimpleNamespace()

    def sync_publish_progress(self, publish_id):
        self.calls.append("sync")
        if self._sync_to is not None:
            self.status = self._sync_to.value
        return SimpleNamespace()


def _handlers(flow):
    tq = Mock()
    return (
        PublishVerifyFlowHandler(flow=flow, task_queue_service=tq),
        PublishOnlineReleaseHandler(flow=flow, task_queue_service=tq),
        PublishProgressPollHandler(flow=flow, task_queue_service=tq, poll_delay_seconds=1.0),
        tq,
    )


# ── verify_flow ─────────────────────────────────────────────────────────────

def test_verify_flow_from_draft_builds_releases_and_enqueues_poll():
    flow = _FakeFlow(PublishStatus.DRAFT)
    verify, _online, _poll, tq = _handlers(flow)
    outcome = verify.handle({"publish_id": 1, "operator": "op"})
    assert isinstance(outcome, Complete)
    assert flow.calls == ["build", "verify_release"]
    assert flow.status == PublishStatus.VALIDATE_PUB.value
    tq.enqueue.assert_called_once()
    assert tq.enqueue.call_args.args[0] == PROGRESS_POLL_TASK


def test_verify_flow_build_failure_stops_without_release_or_poll():
    flow = _FakeFlow(PublishStatus.DRAFT, build_fails=True)
    verify, _online, _poll, tq = _handlers(flow)
    outcome = verify.handle({"publish_id": 1, "operator": "op"})
    assert isinstance(outcome, Complete)
    assert flow.calls == ["build"]
    tq.enqueue.assert_not_called()


def test_verify_flow_resumes_from_building_by_resetting_to_draft():
    flow = _FakeFlow(PublishStatus.BUILDING)
    verify, _online, _poll, tq = _handlers(flow)
    verify.handle({"publish_id": 1, "operator": "op"})
    # reset to DRAFT then full build+release
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
    flow = _FakeFlow(PublishStatus.DRAFT)
    verify, _online, _poll, tq = _handlers(flow)
    verify.handle({"publish_id": 1, "operator": "op"})
    verify.handle({"publish_id": 1, "operator": "op"})  # re-run at VALIDATE_PUB
    assert flow.calls.count("verify_release") == 1
    assert flow.calls.count("build") == 1


# ── online_release ──────────────────────────────────────────────────────────

def test_online_release_from_validating_releases_and_enqueues_poll():
    flow = _FakeFlow(PublishStatus.VALIDATING)
    _verify, online, _poll, tq = _handlers(flow)
    online.handle({"publish_id": 1, "operator": "op"})
    assert flow.calls == ["online_release"]
    assert flow.status == PublishStatus.ONLINE_PUB.value
    tq.enqueue.assert_called_once()


def test_online_release_idempotent_from_success_is_noop():
    flow = _FakeFlow(PublishStatus.SUCCESS)
    _verify, online, _poll, tq = _handlers(flow)
    online.handle({"publish_id": 1, "operator": "op"})
    assert flow.calls == []
    tq.enqueue.assert_not_called()


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


def test_handler_invalid_payload_raises():
    flow = _FakeFlow(PublishStatus.DRAFT)
    verify, _online, _poll, _tq = _handlers(flow)
    with pytest.raises(ValueError):
        verify.handle({"operator": "op"})  # missing publish_id
