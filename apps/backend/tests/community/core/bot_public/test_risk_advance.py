import json
from datetime import datetime
from dataclasses import replace
from unittest.mock import MagicMock

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
