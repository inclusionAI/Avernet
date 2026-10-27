"""Guard risk-linked approval callbacks and preserve early deliveries."""

import json
from datetime import datetime, timedelta, timezone
from typing import Callable

from agentclaw.community.core.bot_public_risk.notices import stage_notice
from agentclaw.community.core.bot_public_risk.repository import BotPublicRiskRepository, RiskCheck


class RiskApprovalGuard:
    def __init__(self, repository: BotPublicRiskRepository, check_h3: Callable[[RiskCheck], bool],
                 clock: Callable[[], datetime] | None = None):
        self.repo = repository
        self.check_h3 = check_h3
        self.clock = clock or (lambda: datetime.now(timezone.utc).replace(tzinfo=None))

    def check(self, env: str, risk_id: str, owner: str, bot_pk: int, puid: str, decision: str, scope: str) -> str:
        if not puid or decision not in {"AGREE", "DISAGREE", "CANCEL"}:
            return "DENIED"
        for _ in range(3):
            row = self.repo.get(env=env, risk_check_id=risk_id)
            if (row is None or row.current_target_key is None or row.bot_pk != bot_pk
                or row.owner_id != owner or row.public_scope != scope or row.security_status != "PASS"):
                return "DENIED"
            workflow = json.loads(row.workflow_json)
            if row.approval_puid:
                if row.phase != "APPROVING" or row.approval_puid != puid:
                    return "DENIED"
                if workflow.get("approval_target_ready") is True:
                    event = {"puid": puid, "decision": decision}
                    claim = workflow.get("callback_claim")
                    if claim:
                        if claim.get("event") != event:
                            return "DENIED"
                        return "ALREADY_APPLIED" if claim.get("status") == "applied" else "DEFERRED"
                    if self.check_h3(row) is True:
                        workflow["callback_claim"] = {"event": event, "status": "processing"}
                        if self.repo.update_current(env=env, risk_check_id=risk_id, row_version=row.row_version,
                                                    changes={"workflow_json": json.dumps(workflow),
                                                             "next_action_at": self.clock() + timedelta(minutes=2)}):
                            return "ALLOW"
                        continue
                    workflow["error"] = "INPUT_OR_PERMISSION_INVALID"
                    changes = {"phase": "NEEDS_ATTENTION", "next_action_at": None}
                    stage_notice(row, workflow, "INPUT_INVALID", self.clock(), changes)
                    changes["workflow_json"] = json.dumps(workflow)
                    self.repo.update_current(env=env, risk_check_id=risk_id, row_version=row.row_version, changes=changes)
                    return "DENIED"
            elif (row.phase != "ADVANCING" and not (
                row.phase == "NEEDS_ATTENTION" and workflow.get("error") == "APPROVAL_OUTCOME_UNKNOWN"
            )) or workflow.get("approval_intent") is not True:
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

    def mark_target_ready(self, env: str, risk_id: str, puid: str) -> None:
        for _ in range(3):
            row = self.repo.get(env=env, risk_check_id=risk_id)
            if row is None or row.current_target_key is None or row.approval_puid != puid:
                raise RuntimeError("risk approval association unavailable")
            workflow = json.loads(row.workflow_json)
            recovered = row.phase == "NEEDS_ATTENTION" and workflow.get("error") == "APPROVAL_TARGET_NOT_READY"
            if row.phase != "APPROVING" and not recovered:
                raise RuntimeError("risk approval is not awaiting its target")
            if recovered:
                if self.check_h3(row) is not True:
                    raise RuntimeError("risk eligibility changed before target recovery")
                workflow.pop("error", None)
            workflow["approval_target_ready"] = True
            changes = {"workflow_json": json.dumps(workflow), "next_action_at": None}
            if recovered:
                changes["phase"] = "APPROVING"
            if workflow.get("pending_approval"):
                changes["next_action_at"] = self.clock()
            if self.repo.update_current(env=env, risk_check_id=risk_id, row_version=row.row_version, changes=changes):
                return
        raise RuntimeError("unable to mark approval target ready")

    def finish(self, env: str, risk_id: str, puid: str, decision: str, success: bool) -> None:
        event = {"puid": puid, "decision": decision}
        for _ in range(3):
            row = self.repo.get(env=env, risk_check_id=risk_id)
            if row is None or row.current_target_key is None:
                raise RuntimeError("risk callback association lost")
            workflow = json.loads(row.workflow_json)
            claim = workflow.get("callback_claim") or {}
            if claim.get("event") != event or claim.get("status") != "processing":
                raise RuntimeError("risk callback claim lost")
            claim["status"] = "applied" if success else "unknown"
            changes = {"workflow_json": json.dumps(workflow), "next_action_at": None}
            if success and row.phase == "NEEDS_ATTENTION" and workflow.get("error") == "CALLBACK_OUTCOME_UNKNOWN":
                workflow.pop("error", None)
                changes.update({"phase": "APPROVING", "workflow_json": json.dumps(workflow)})
            if not success:
                workflow["error"] = "CALLBACK_OUTCOME_UNKNOWN"
                changes.update({"phase": "NEEDS_ATTENTION", "next_action_at": None,
                                "workflow_json": json.dumps(workflow)})
            if self.repo.update_current(env=env, risk_check_id=risk_id, row_version=row.row_version, changes=changes):
                return
        raise RuntimeError("unable to persist risk callback outcome")

    def replay(self, env: str, risk_id: str, callback: Callable[[str, str], dict]) -> bool:
        row = self.repo.get(env=env, risk_check_id=risk_id)
        if row is None:
            return False
        workflow = json.loads(row.workflow_json)
        event = workflow.get("pending_approval")
        if not isinstance(event, dict):
            return False
        puid, decision = event.get("puid"), event.get("decision")
        if (row.current_target_key is None or row.phase != "APPROVING" or row.approval_puid != puid
                or workflow.get("approval_target_ready") is not True):
            return False
        if self.check_h3(row) is not True:
            self.check(env, risk_id, row.owner_id, row.bot_pk, puid, decision, row.public_scope)
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
            if (state.get("callback_claim") or {}).get("status") != "applied":
                return False
            del state["pending_approval"]
            if self.repo.update_current(env=env, risk_check_id=risk_id, row_version=current.row_version,
                                        changes={"workflow_json": json.dumps(state), "next_action_at": None}):
                return True
        raise RuntimeError("unable to complete approval callback replay")
