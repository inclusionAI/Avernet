"""Guard risk-linked approval callbacks and preserve early deliveries."""

import json
from typing import Callable

from agentclaw.community.core.bot_public_risk.repository import BotPublicRiskRepository, RiskCheck


class RiskApprovalGuard:
    def __init__(self, repository: BotPublicRiskRepository, check_h3: Callable[[RiskCheck], bool]):
        self.repo = repository
        self.check_h3 = check_h3

    def check(self, env: str, risk_id: str, owner: str, bot_pk: int, puid: str, decision: str, scope: str) -> str:
        if not puid or decision not in {"AGREE", "DISAGREE", "CANCEL"}:
            return "DENIED"
        for _ in range(3):
            row = self.repo.get(env=env, risk_check_id=risk_id)
            if (row is None or row.current_target_key is None or row.bot_pk != bot_pk
                or row.owner_id != owner or row.public_scope != scope or row.security_status != "PASS"):
                return "DENIED"
            if row.approval_puid:
                if row.phase != "APPROVING" or row.approval_puid != puid:
                    return "DENIED"
                return "ALLOW" if self.check_h3(row) is True else "DENIED"
            workflow = json.loads(row.workflow_json)
            if row.phase != "ADVANCING" or workflow.get("approval_intent") is not True:
                return "DENIED"
            event = {"puid": puid, "decision": decision}
            pending = workflow.get("pending_approval")
            if pending is not None:
                return "DEFERRED" if pending == event else "DENIED"
            workflow["pending_approval"] = event
            if self.repo.update_current(env=env, risk_check_id=risk_id, row_version=row.row_version,
                                        changes={"workflow_json": json.dumps(workflow)}):
                return "DEFERRED"
        raise RuntimeError("unable to persist early approval callback")

    def replay(self, env: str, risk_id: str, callback: Callable[[str, str], dict]) -> bool:
        row = self.repo.get(env=env, risk_check_id=risk_id)
        if row is None:
            return False
        workflow = json.loads(row.workflow_json)
        event = workflow.get("pending_approval")
        if not isinstance(event, dict):
            return False
        puid, decision = event.get("puid"), event.get("decision")
        if self.check(env, risk_id, row.owner_id, row.bot_pk, puid, decision, row.public_scope) != "ALLOW":
            return False
        if not callback(puid, decision).get("success"):
            return False
        # Preserve other concurrent workflow updates when removing the event.
        for _ in range(3):
            current = self.repo.get(env=env, risk_check_id=risk_id)
            if current is None or current.current_target_key is None:
                return False
            state = json.loads(current.workflow_json)
            if state.get("pending_approval") != event:
                return False
            del state["pending_approval"]
            if self.repo.update_current(env=env, risk_check_id=risk_id, row_version=current.row_version,
                                        changes={"workflow_json": json.dumps(state)}):
                return True
        raise RuntimeError("unable to complete approval callback replay")
