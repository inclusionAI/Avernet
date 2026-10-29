"""The first submit retains the creation report without a report-query round trip."""
import json

import pytest

from agentclaw.community.core.bot_public_risk.tasks import SecurityReport, SecuritySubmission
from tests.community.core.bot_public.test_risk_tasks import make


@pytest.mark.parametrize("status,phase,task", [
    ("DETECTING", "CHECKING", "bot_public_risk_detecting"),
    ("REJECT", "CHECKING", "bot_public_risk_reject"),
    ("PASS", "ADVANCING", "bot_public_risk_advance"),
])
def test_first_submit_returns_creation_report_and_schedules_followup(status, phase, task):
    tasks, repo, queue, provider, _, record = make()
    calls = []

    def submit(payload):
        calls.append(payload)
        return SecuritySubmission(check_id="check-1", report=SecurityReport(
            status, "https://example.test/check-1", valid_pass=status == "PASS"))

    provider.submit_agent_scan = submit
    saved, created = tasks.start(record)
    assert created and saved.phase == phase
    assert saved.security_status == status and saved.check_id == "check-1"
    assert saved.detail_url == "https://example.test/check-1"
    assert len(calls) == 1 and provider.queries == []
    assert queue.entries[-1][0][0] == task
    assert not any(entry[0][0] == "bot_public_risk_submit" for entry in queue.entries)
    replay, created = tasks.start(record)
    assert not created and replay == repo.get(env="dev", risk_check_id="a")
    assert len(calls) == 1


def test_sync_timeout_is_persisted_and_replay_does_not_create_again():
    tasks, repo, _, provider, _, record = make()
    calls = []

    def submit(payload):
        calls.append(payload)
        raise TimeoutError("response unknown")

    provider.submit_agent_scan = submit
    saved, _ = tasks.start(record)
    assert saved.phase == "NEEDS_ATTENTION"
    assert json.loads(saved.workflow_json)["error"] == "SUBMISSION_OUTCOME_UNKNOWN"
    tasks.start(record)
    tasks.submit(env="dev", risk_check_id="a")
    assert len(calls) == 1


def test_later_report_without_url_keeps_creation_link():
    tasks, repo, _, provider, _, record = make()
    provider.submit_agent_scan = lambda payload: SecuritySubmission(
        check_id="check-1", report=SecurityReport("DETECTING", "https://example.test/check-1"))
    tasks.start(record)
    provider.report = SecurityReport("PASS", valid_pass=True)
    tasks.poll(env="dev", risk_check_id="a", manual=True)
    assert repo.get(env="dev", risk_check_id="a").detail_url == "https://example.test/check-1"


def test_recovery_during_sync_creation_does_not_enqueue_another_submit():
    tasks, repo, queue, provider, now, record = make()
    calls = []

    def submit(payload):
        calls.append(payload)
        tasks.recover(env="dev", now=now[0])
        replay, created = tasks.start(record)
        assert not created and replay.phase == "PREPARING"
        return SecuritySubmission("check-1", SecurityReport("DETECTING"))

    provider.submit_agent_scan = submit
    saved, _ = tasks.start(record)
    assert saved.phase == "CHECKING" and len(calls) == 1
    assert [e[0][0] for e in queue.entries] == ["bot_public_risk_detecting"]


def test_queue_failure_after_sync_creation_preserves_result_for_recovery():
    tasks, repo, queue, provider, now, record = make()
    provider.submit_agent_scan = lambda payload: SecuritySubmission(
        "check-1", SecurityReport("DETECTING", "https://example.test/check-1"))
    original = queue.enqueue
    queue.enqueue = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("queue down"))
    with pytest.raises(RuntimeError, match="queue down"):
        tasks.start(record)
    saved = repo.get(env="dev", risk_check_id="a")
    assert saved.check_id == "check-1" and saved.detail_url == "https://example.test/check-1"
    queue.enqueue = original
    tasks.recover(env="dev", now=saved.next_action_at)
    assert queue.entries[-1][0][0] == "bot_public_risk_detecting"


def test_initial_unverified_pass_does_not_schedule_approval():
    tasks, _, queue, provider, _, record = make()
    provider.submit_agent_scan = lambda payload: SecuritySubmission(
        "check-1", SecurityReport("PASS", "https://example.test/check-1"))
    saved, _ = tasks.start(record)
    assert saved.phase == "NEEDS_ATTENTION" and saved.detail_url
    assert not any(e[0][0] == "bot_public_risk_advance" for e in queue.entries)


def test_missing_creation_report_preserves_id_and_recovers_original_check():
    tasks, repo, queue, provider, _, record = make()
    provider.submit_agent_scan = lambda payload: SecuritySubmission("check-1", None)
    saved, _ = tasks.start(record)
    assert saved.check_id == "check-1" and saved.phase == "CHECKING"
    assert json.loads(saved.workflow_json)["error"] == "INITIAL_REPORT_UNAVAILABLE"
    assert queue.entries[-1][1]["delay_seconds"] == 0
    provider.report = SecurityReport("PASS", "https://example.test/check-1", valid_pass=True)
    tasks.poll(env="dev", risk_check_id="a", manual=True)
    saved = repo.get(env="dev", risk_check_id="a")
    assert saved.phase == "ADVANCING" and saved.detail_url
    assert provider.queries == ["check-1"]
    assert "error" not in json.loads(saved.workflow_json)
