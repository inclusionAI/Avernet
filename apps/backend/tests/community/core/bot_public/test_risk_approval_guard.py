import json
from dataclasses import replace
from unittest.mock import MagicMock

from agentclaw.community.core.bot_public_risk.approval_guard import RiskApprovalGuard
from agentclaw.community.core.bot_public_risk.repository import RiskCheck


def _record(**updates):
    return replace(RiskCheck(
        risk_check_id="risk-a", env="dev", bot_pk=42, owner_id="owner", operator_id="applicant",
        client_scope="openapi:applicant", client_request_id="req", public_scope="user", target_json="{}",
        request_hash="request", input_json="{}", input_hash="hash", approval_biz_id="biz-risk-a",
        phase="ADVANCING", security_status="PASS", current_target_key="current", workflow_json='{"approval_intent":true}',
    ), **updates)


def test_early_callback_is_persisted_in_current_record_and_not_applied():
    repo = MagicMock()
    repo.get.return_value = _record()
    repo.update_current.return_value = True
    guard = RiskApprovalGuard(repo, MagicMock(return_value=True))
    assert guard.check("dev", "risk-a", "owner", 42, "puid-a", "AGREE", "user") == "DEFERRED"
    changes = repo.update_current.call_args.kwargs["changes"]
    assert json.loads(changes["workflow_json"])["pending_approval"] == {"puid": "puid-a", "decision": "AGREE"}


def test_approved_callback_requires_original_puid_and_h3():
    repo = MagicMock()
    repo.get.return_value = _record(phase="APPROVING", approval_puid="puid-a", workflow_json='{"approval_target_ready":true}')
    h3 = MagicMock(return_value=False)
    guard = RiskApprovalGuard(repo, h3)
    assert guard.check("dev", "risk-a", "owner", 42, "puid-b", "AGREE", "user") == "DENIED"
    assert guard.check("dev", "risk-a", "owner", 42, "puid-a", "AGREE", "user") == "DENIED"
    h3.return_value = True
    assert guard.check("dev", "risk-a", "owner", 42, "puid-a", "AGREE", "user") == "ALLOW"


def test_pending_callback_replayed_only_after_puid_association_and_h3():
    row = _record(phase="APPROVING", approval_puid="puid-a", workflow_json=json.dumps({
        "approval_intent": True, "approval_target_ready": True, "pending_approval": {"puid": "puid-a", "decision": "AGREE"}
    }))
    repo = MagicMock()
    repo.get.return_value = row
    repo.update_current.return_value = True
    def apply(puid, decision):
        state = json.loads(row.workflow_json)
        state["callback_claim"] = {"event": {"puid": puid, "decision": decision}, "status": "applied"}
        repo.get.return_value = replace(row, workflow_json=json.dumps(state))
        return {"success": True}
    callback = MagicMock(side_effect=apply)
    guard = RiskApprovalGuard(repo, MagicMock(return_value=True))
    assert guard.replay("dev", "risk-a", callback) is True
    callback.assert_called_once_with("puid-a", "AGREE")
    changes = repo.update_current.call_args.kwargs["changes"]
    assert "pending_approval" not in json.loads(changes["workflow_json"])


def test_deferred_callback_remains_pending_if_h3_is_unavailable():
    row = _record(phase="APPROVING", approval_puid="puid-a", workflow_json=json.dumps({
        "approval_target_ready": True, "pending_approval": {"puid": "puid-a", "decision": "AGREE"}
    }))
    repo = MagicMock()
    repo.get.return_value = row
    callback = MagicMock()
    assert RiskApprovalGuard(repo, lambda _: False).replay("dev", "risk-a", callback) is False
    callback.assert_not_called()
    changes = repo.update_current.call_args.kwargs["changes"]
    assert json.loads(changes["workflow_json"])["pending_approval"]["puid"] == "puid-a"


def test_h3_input_failure_stages_applicant_notification():
    from datetime import datetime
    repo = MagicMock()
    repo.get.return_value = _record(phase="APPROVING", approval_puid="puid-a", workflow_json='{"approval_target_ready":true}')
    repo.update_current.return_value = True
    guard = RiskApprovalGuard(repo, lambda _: False, clock=lambda: datetime(2026, 1, 1))
    assert guard.check("dev", "risk-a", "owner", 42, "puid-a", "AGREE", "user") == "DENIED"
    changes = repo.update_current.call_args.kwargs["changes"]
    assert changes["phase"] == "NEEDS_ATTENTION"
    assert json.loads(changes["workflow_json"])["notices"]["INPUT_INVALID"]["status"] == "pending"


def test_associated_callback_waits_until_approval_target_is_saved():
    repo = MagicMock()
    repo.get.return_value = _record(phase="APPROVING", approval_puid="puid-a")
    repo.update_current.return_value = True
    h3 = MagicMock(return_value=True)
    guard = RiskApprovalGuard(repo, h3)
    assert guard.check("dev", "risk-a", "owner", 42, "puid-a", "AGREE", "user") == "DEFERRED"
    h3.assert_not_called()
    changes = repo.update_current.call_args.kwargs["changes"]
    assert json.loads(changes["workflow_json"])["pending_approval"]["puid"] == "puid-a"


def test_concurrent_callbacks_claim_once_and_late_decision_cannot_overwrite():
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier, Lock
    from dataclasses import replace

    class Repository:
        def __init__(self):
            self.row = _record(phase="APPROVING", approval_puid="puid-a", workflow_json='{"approval_target_ready":true}')
            self.lock = Lock()
        def get(self, **_):
            with self.lock:
                return self.row
        def update_current(self, *, row_version, changes, **_):
            with self.lock:
                if self.row.row_version != row_version:
                    return False
                self.row = replace(self.row, **changes, row_version=row_version + 1)
                return True
    repo, barrier = Repository(), Barrier(2)
    def h3(_):
        barrier.wait(timeout=5)
        return True
    guard = RiskApprovalGuard(repo, h3)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(guard.check, "dev", "risk-a", "owner", 42, "puid-a", "AGREE", "user") for _ in range(2)]
        assert sorted(f.result() for f in futures) == ["ALLOW", "DEFERRED"]
    guard.finish("dev", "risk-a", "puid-a", "AGREE", True)
    assert guard.check("dev", "risk-a", "owner", 42, "puid-a", "AGREE", "user") == "ALREADY_APPLIED"
    assert guard.check("dev", "risk-a", "owner", 42, "puid-a", "CANCEL", "user") == "DENIED"


def test_late_target_ready_rechecks_eligibility_and_recovers_same_approval():
    repo = MagicMock()
    repo.get.return_value = _record(phase="NEEDS_ATTENTION", approval_puid="puid-a", workflow_json=json.dumps({
        "approval_intent": True, "error": "APPROVAL_TARGET_NOT_READY", "pending_approval": {"puid": "puid-a", "decision": "AGREe"}
    }))
    repo.update_current.return_value = True
    h3 = MagicMock(return_value=True)
    guard = RiskApprovalGuard(repo, h3)
    guard.mark_target_ready("dev", "risk-a", "puid-a")
    changes = repo.update_current.call_args.kwargs["changes"]
    assert changes["phase"] == "APPROVING"
    assert json.loads(changes["workflow_json"])["approval_target_ready"] is True
    h3.assert_called_once()


def test_inline_completed_event_survives_h3_exception_for_recovery():
    from datetime import datetime
    from dataclasses import replace
    import pytest
    class Repository:
        def __init__(self):
            self.row = _record(phase="APPROVING", approval_puid="puid-a")
        def get(self, **_):
            return self.row
        def update_current(self, *, row_version, changes, **_):
            if self.row.row_version != row_version:
                return False
            self.row = replace(self.row, **changes, row_version=row_version + 1)
            return True
    repo = Repository()
    now = datetime(2026, 1, 1)
    def unavailable(_):
        raise RuntimeError("H3 unavailable")
    guard = RiskApprovalGuard(repo, unavailable, clock=lambda: now)
    assert guard.check("dev", "risk-a", "owner", 42, "puid-a", "AGREE", "user") == "DEFERRED"
    guard.mark_target_ready("dev", "risk-a", "puid-a")
    with pytest.raises(RuntimeError, match="H3 unavailable"):
        guard.replay("dev", "risk-a", MagicMock())
    assert json.loads(repo.row.workflow_json)["pending_approval"]["decision"] == "AGREE"
    assert repo.row.next_action_at == now
