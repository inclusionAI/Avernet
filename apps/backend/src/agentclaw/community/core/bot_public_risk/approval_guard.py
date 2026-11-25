"""Guard risk-linked approval callbacks and preserve early deliveries."""

import json
import re
from datetime import datetime, timedelta, timezone
from typing import Callable

from agentclaw.community.core.bot_public_risk.eligibility import (
    BLOCK_NOTICE,
    EligibilityCheck,
    evaluate_eligibility,
    log_block,
)
from agentclaw.community.core.bot_public_risk.notices import stage_notice
from agentclaw.community.core.bot_public_risk.repository_protocol import RiskRepository, RiskCheck


class RiskApprovalGuard:
    def __init__(self, repository: RiskRepository, check_h3: EligibilityCheck,
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
                claim = workflow.get("callback_claim") or {}
                event = {"puid": puid, "decision": decision}
                if claim.get("status") == "applied" and claim.get("event") == event and row.approval_puid == puid:
                    return "ALREADY_APPLIED"
                recovered_pending = row.phase == "NEEDS_ATTENTION" and workflow.get("error") == "APPROVAL_TARGET_NOT_READY"
                if (row.phase != "APPROVING" and not recovered_pending) or row.approval_puid != puid:
                    return "DENIED"
                if workflow.get("approval_target_ready") is True:
                    if claim:
                        if claim.get("event") != event:
                            return "DENIED"
                        return "ALREADY_APPLIED" if claim.get("status") == "applied" else "DEFERRED"
                    workflow.update(approval_decision=decision, approval_decision_puid=puid)
                    if decision == "AGREE":
                        eligibility = evaluate_eligibility(self.check_h3, row)
                        if not eligibility.ok:
                            assert eligibility.reason is not None
                            changes = self._blocked_changes(row, workflow, eligibility.reason)
                            if self.repo.update_current(env=env, risk_check_id=risk_id,
                                                        row_version=row.row_version, changes=changes):
                                log_block(row, eligibility, gate="H3", puid=puid)
                                return "DENIED"
                            continue
                    workflow["callback_claim"] = {"event": event, "status": "processing"}
                    if self.repo.update_current(env=env, risk_check_id=risk_id, row_version=row.row_version,
                                                changes={"workflow_json": json.dumps(workflow),
                                                         "next_action_at": self.clock() + timedelta(minutes=2)}):
                        return "ALLOW"
                    continue
            elif (row.phase != "ADVANCING" and not (
                row.phase == "NEEDS_ATTENTION" and workflow.get("error") == "APPROVAL_OUTCOME_UNKNOWN"
            )) or workflow.get("approval_intent") is not True:
                return "DENIED"
            # Before association only a PUID for this generated risk business ID
            # is eligible for deferred handling; the returned PUID remains authoritative.
            if re.fullmatch(r"risk-[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}", row.approval_biz_id):
                if not re.fullmatch(r"antprocess-agentclaw_botpublic_" + re.escape(row.approval_biz_id) + r"(?:_.+)?", puid):
                    return "DENIED"
            event = {"puid": puid, "decision": decision}
            pending = workflow.get("pending_approval")
            if pending is not None:
                candidates = workflow.get("pending_candidates", [])
                if not isinstance(candidates, list):
                    return "DENIED"
                if pending == event or event in candidates:
                    return "DEFERRED"
                if len(candidates) >= 7:
                    return "DENIED"
                if any(item.get("puid") == puid for item in [pending, *candidates] if isinstance(item, dict)):
                    return "DENIED"
                workflow["pending_candidates"] = [*candidates, event]
            else:
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
                pending_decision = (workflow.get("pending_approval") or {}).get("decision")
                if pending_decision not in {"DISAGREE", "CANCEL"} and not evaluate_eligibility(self.check_h3, row).ok:
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

    def _blocked_changes(self, row: RiskCheck, workflow: dict, reason: str) -> dict:
        workflow["error"] = reason
        changes = {"phase": "NEEDS_ATTENTION", "next_action_at": None}
        stage_notice(row, workflow, BLOCK_NOTICE[reason], self.clock(), changes)
        changes["workflow_json"] = json.dumps(workflow)
        return changes

    def finish(self, env: str, risk_id: str, puid: str, decision: str, success: bool) -> None:
        event = {"puid": puid, "decision": decision}
        for _ in range(3):
            row = self.repo.get(env=env, risk_check_id=risk_id)
            if row is None or row.current_target_key is None:
                raise RuntimeError("risk callback association lost")
            workflow = json.loads(row.workflow_json)
            claim = workflow.get("callback_claim") or {}
            if not success and claim.get("event") == event and claim.get("status") == "blocked":
                return
            if claim.get("event") != event or claim.get("status") != "processing":
                raise RuntimeError("risk callback claim lost")
            workflow.update(approval_decision=decision, approval_decision_puid=puid)
            blocked = claim.get("block_reason")
            if not success and blocked in BLOCK_NOTICE:
                claim["status"] = "blocked"
                changes = self._blocked_changes(row, workflow, blocked)
            elif success:
                claim["status"] = "applied"
                workflow.pop("error", None)
                changes = {"phase": "PUBLISHED" if decision == "AGREE" else "CLOSED",
                           "next_action_at": None, "workflow_json": json.dumps(workflow)}
            else:
                claim["status"] = "unknown"
                workflow["error"] = "CALLBACK_OUTCOME_UNKNOWN"
                changes = {"phase": "NEEDS_ATTENTION", "next_action_at": None,
                           "workflow_json": json.dumps(workflow)}
            if self.repo.update_current(env=env, risk_check_id=risk_id, row_version=row.row_version, changes=changes):
                return
        raise RuntimeError("unable to persist risk callback outcome")

    def revalidate_claim(self, env: str, risk_id: str, puid: str, decision: str) -> bool:
        """Recheck the claim and, for agreement, the publish eligibility before applying it."""
        for _ in range(3):
            row = self.repo.get(env=env, risk_check_id=risk_id)
            if row is None or row.current_target_key is None or row.phase != "APPROVING" or row.approval_puid != puid:
                return False
            workflow = json.loads(row.workflow_json)
            claim = workflow.get("callback_claim") or {}
            if claim.get("status") != "processing" or claim.get("event") != {"puid": puid, "decision": decision}:
                return False
            if decision != "AGREE":
                return True
            if claim.get("block_reason"):
                return False
            eligibility = evaluate_eligibility(self.check_h3, row)
            if eligibility.ok:
                return True
            assert eligibility.reason is not None
            claim["block_reason"] = eligibility.reason
            if self.repo.update_current(env=env, risk_check_id=risk_id, row_version=row.row_version,
                                        changes={"workflow_json": json.dumps(workflow)}):
                log_block(row, eligibility, gate="prewrite", puid=puid)
                return False
        raise RuntimeError("unable to persist prewrite eligibility block")

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
