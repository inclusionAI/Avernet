import json
from datetime import datetime
from dataclasses import replace
from unittest.mock import MagicMock

import pytest

from agentclaw.community.core.bot_public_risk.advance import RiskAdvance
from agentclaw.community.core.bot_public_risk.repository import RiskCheck


def row(**kwargs):
    return replace(RiskCheck(risk_check_id="a", env="dev", bot_pk=1, owner_id="owner", operator_id="actor",
        client_scope="openapi:actor", client_request_id="request", public_scope="user", target_json="{}",
        request_hash="request", input_json="{}", input_hash="hash", approval_biz_id="biz-a",
        phase="ADVANCING", security_status="PASS", current_target_key="current"), **kwargs)


def test_existing_approval_intent_never_blindly_resubmits():
    repo = MagicMock()
    repo.get.return_value = row(workflow_json='{"approval_intent":true}')
    repo.update_current.return_value = True
    submit = MagicMock()
    RiskAdvance(repo, lambda _: True, submit, lambda: datetime(2026, 1, 1)).advance("dev", "a")
    submit.assert_not_called()
    assert repo.update_current.call_args.kwargs["changes"]["phase"] == "NEEDS_ATTENTION"


def test_h2_failure_never_submits():
    repo = MagicMock()
    repo.get.return_value = row()
    repo.update_current.return_value = True
    submit = MagicMock()
    RiskAdvance(repo, lambda _: False, submit, lambda: datetime(2026, 1, 1)).advance("dev", "a")
    submit.assert_not_called()
    assert repo.update_current.call_args.kwargs["changes"]["phase"] == "NEEDS_ATTENTION"


class MemoryRepository:
    def __init__(self, initial):
        self.row = initial

    def get(self, *, env, risk_check_id):
        return self.row if (self.row.env, self.row.risk_check_id) == (env, risk_check_id) else None

    def update_current(self, *, env, risk_check_id, row_version, changes):
        if self.row.current_target_key is None or self.row.row_version != row_version:
            return False
        self.row = replace(self.row, **changes, row_version=row_version + 1)
        return True


def test_inline_approval_is_associated_before_callback():
    repo = MemoryRepository(row())
    def submit(record, on_started):
        assert json.loads(record.workflow_json)["approval_intent"] is True
        on_started({"puid": "p1"})
        assert repo.row.phase == "APPROVING" and repo.row.approval_puid == "p1"
        return "approval"
    RiskAdvance(repo, lambda _: True, submit, lambda: datetime(2026, 1, 1)).advance("dev", "a")
    assert repo.row.phase == "APPROVING"


def test_external_creation_exception_does_not_trigger_second_submission():
    repo = MemoryRepository(row())
    submit = MagicMock(side_effect=TimeoutError("unknown"))
    advance = RiskAdvance(repo, lambda _: True, submit, lambda: datetime(2026, 1, 1))
    advance.advance("dev", "a")
    advance.advance("dev", "a")
    assert repo.row.phase == "NEEDS_ATTENTION"
    assert json.loads(repo.row.workflow_json)["error"] == "APPROVAL_OUTCOME_UNKNOWN"
    assert submit.call_count == 1


def test_early_callback_keeps_recovery_due_until_replayed():
    repo = MemoryRepository(row())
    now = datetime(2026, 1, 1)
    def submit(record, on_started):
        workflow = json.loads(repo.row.workflow_json)
        workflow["pending_approval"] = {"puid": "p1", "decision": "AGREE"}
        repo.update_current(env="dev", risk_check_id="a", row_version=repo.row.row_version,
                            changes={"workflow_json": json.dumps(workflow)})
        on_started({"puid": "p1"})
        return "approval"
    RiskAdvance(repo, lambda _: True, submit, lambda: now).advance("dev", "a")
    assert repo.row.phase == "APPROVING"
    assert repo.row.next_action_at == now


def test_advance_handler_binds_tenant_and_rejects_wrong_environment():
    from agentclaw.community.core.bot_public_risk.advance import RiskAdvanceHandler
    from agentclaw.community.utils.avernet_tenant import get_current_avernet_tenant

    advance = MagicMock()
    observed = []
    advance.advance.side_effect = lambda env, risk_id: observed.append((get_current_avernet_tenant(), env, risk_id))
    handler = RiskAdvanceHandler(advance, lambda: "dev")
    handler.handle({"tenant": "tenant-x", "env": "dev", "risk_check_id": "a"})
    assert observed == [("tenant-x", "dev", "a")]
    with pytest.raises(ValueError):
        handler.handle({"tenant": "tenant-x", "env": "prod", "risk_check_id": "a"})


def test_recovery_enqueues_pending_approval_callback():
    from agentclaw.community.core.bot_public_risk.tasks import REPLAY, RiskTasks
    repo = MagicMock()
    pending = row(phase="APPROVING", approval_puid="p1", next_action_at=datetime(2026, 1, 1),
                  workflow_json='{"pending_approval":{"puid":"p1","decision":"AGREE"}}')
    repo.list_due.return_value = (pending,)
    queue = MagicMock()
    tasks = RiskTasks(repo, queue, MagicMock(), lambda: datetime(2026, 1, 1))
    tasks.recover(env="dev", now=datetime(2026, 1, 1))
    assert queue.enqueue.call_args.args[0] == REPLAY


def test_pending_handler_replays_against_original_bot_and_applicant():
    from agentclaw.community.core.bot_public_risk.advance import RiskPendingHandler
    repo = MagicMock()
    repo.get.return_value = row(phase="APPROVING", approval_puid="p1", workflow_json='{"pending_approval":{}}')
    bots = MagicMock()
    bots.get_by_pk.return_value = {"bot_id": "b", "entity_id": "e"}
    guard = MagicMock()
    def replay(env, risk_id, callback):
        callback("p1", "AGREE")
        return True
    guard.replay.side_effect = replay
    public = MagicMock()
    handler = RiskPendingHandler(repo, bots, public, guard, lambda: "dev")
    handler.handle({"tenant": "teamclaw", "env": "dev", "risk_check_id": "a"})
    kwargs = public.handle_public_approval_callback.call_args.kwargs
    assert kwargs["bot_id"] == "b:e" and kwargs["owner_id"] == "owner"
    assert kwargs["risk_check_id"] == "a" and kwargs["puid"] == "p1"


def test_h2_invalidation_stages_notice_for_actual_openapi_applicant():
    repo = MemoryRepository(row())
    RiskAdvance(repo, lambda _: False, MagicMock(), lambda: datetime(2026, 1, 1)).advance("dev", "a")
    assert repo.row.phase == "NEEDS_ATTENTION"
    assert json.loads(repo.row.workflow_json)["notices"]["INPUT_INVALID"]["status"] == "pending"
    assert repo.row.notification_due_at == datetime(2026, 1, 1)


def test_concurrent_reclaimed_worker_does_not_lose_first_approval_puid():
    from threading import Event, Thread
    repo = MemoryRepository(row())
    sent, resume = Event(), Event()
    def submit(record, on_started):
        sent.set()
        assert resume.wait(5)
        on_started({"puid": "p1"})
        return "approval"
    advance = RiskAdvance(repo, lambda _: True, submit, lambda: datetime(2026, 1, 1))
    thread = Thread(target=advance.advance, args=("dev", "a"))
    thread.start()
    assert sent.wait(5)
    advance.advance("dev", "a")
    resume.set()
    thread.join(5)
    assert not thread.is_alive()
    assert repo.row.phase == "APPROVING" and repo.row.approval_puid == "p1"


def test_approval_target_write_failure_is_recorded_without_restarting_approval():
    repo = MemoryRepository(row())
    def submit(record, on_started):
        on_started({"puid": "p1"})
        raise RuntimeError("target write failed")
    advance = RiskAdvance(repo, lambda _: True, submit, lambda: datetime(2026, 1, 1))
    advance.advance("dev", "a")
    assert repo.row.approval_puid == "p1"
    assert repo.row.phase == "NEEDS_ATTENTION"
    assert json.loads(repo.row.workflow_json)["error"] == "APPROVAL_TARGET_NOT_READY"


def test_recovery_marks_interrupted_callback_unknown_without_reapplying():
    from agentclaw.community.core.bot_public_risk.advance import RiskPendingHandler
    state = {"approval_target_ready": True, "callback_claim": {
        "event": {"puid": "p1", "decision": "AGREE"}, "status": "processing"}}
    repo = MemoryRepository(row(phase="APPROVING", approval_puid="p1", workflow_json=json.dumps(state)))
    public, guard = MagicMock(), MagicMock()
    handler = RiskPendingHandler(repo, MagicMock(), public, guard, lambda: "dev")
    handler.handle({"tenant": "teamclaw", "env": "dev", "risk_check_id": "a"})
    assert repo.row.phase == "NEEDS_ATTENTION"
    assert json.loads(repo.row.workflow_json)["error"] == "CALLBACK_OUTCOME_UNKNOWN"
    public.handle_public_approval_callback.assert_not_called()
    guard.replay.assert_not_called()
