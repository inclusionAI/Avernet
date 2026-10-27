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
    repo.get.return_value = _record(phase="APPROVING", approval_puid="puid-a")
    h3 = MagicMock(return_value=False)
    guard = RiskApprovalGuard(repo, h3)
    assert guard.check("dev", "risk-a", "owner", 42, "puid-b", "AGREE", "user") == "DENIED"
    assert guard.check("dev", "risk-a", "owner", 42, "puid-a", "AGREE", "user") == "DENIED"
    h3.return_value = True
    assert guard.check("dev", "risk-a", "owner", 42, "puid-a", "AGREE", "user") == "ALLOW"


def test_pending_callback_replayed_only_after_puid_association_and_h3():
    row = _record(phase="APPROVING", approval_puid="puid-a", workflow_json=json.dumps({
        "approval_intent": True, "pending_approval": {"puid": "puid-a", "decision": "AGREE"}
    }))
    repo = MagicMock()
    repo.get.return_value = row
    repo.update_current.return_value = True
    callback = MagicMock(return_value={"success": True})
    guard = RiskApprovalGuard(repo, MagicMock(return_value=True))
    assert guard.replay("dev", "risk-a", callback) is True
    callback.assert_called_once_with("puid-a", "AGREE")
    changes = repo.update_current.call_args.kwargs["changes"]
    assert "pending_approval" not in json.loads(changes["workflow_json"])


def test_deferred_callback_remains_pending_if_h3_is_unavailable():
    row = _record(phase="APPROVING", approval_puid="puid-a", workflow_json=json.dumps({
        "pending_approval": {"puid": "puid-a", "decision": "AGREE"}
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
    repo.get.return_value = _record(phase="APPROVING", approval_puid="puid-a")
    repo.update_current.return_value = True
    guard = RiskApprovalGuard(repo, lambda _: False, clock=lambda: datetime(2026, 1, 1))
    assert guard.check("dev", "risk-a", "owner", 42, "puid-a", "AGREE", "user") == "DENIED"
    changes = repo.update_current.call_args.kwargs["changes"]
    assert changes["phase"] == "NEEDS_ATTENTION"
    assert json.loads(changes["workflow_json"])["notices"]["INPUT_INVALID"]["status"] == "pending"
