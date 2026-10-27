"""One-shot handoff of a validated security result to the publish service."""

import json
from datetime import datetime, timedelta, timezone
from typing import Callable

from agentclaw.community.core.bot_public_risk.repository import BotPublicRiskRepository, RiskCheck
from agentclaw.community.core.bot_public_risk.notices import stage_notice


class RiskAdvance:
    """H2 and publish are injected; an uncertain creation is never retried blindly.

    The publish adapter calls on_started with a PUID *before* any inline callback,
    and returns 'approval' or 'published'. The injected H2 must rebuild the same
    input digest and check current owner and the original operator's permission.
    """

    def __init__(self, repository: BotPublicRiskRepository, check_h2: Callable[[RiskCheck], bool],
                 submit: Callable, clock: Callable):
        self.repo, self.check_h2, self.submit, self.clock = repository, check_h2, submit, clock

    def _attention(self, row: RiskCheck, reason: str) -> None:
        workflow = json.loads(row.workflow_json)
        workflow["error"] = reason
        changes = {"phase": "NEEDS_ATTENTION", "next_action_at": None}
        if reason == "INPUT_OR_PERMISSION_INVALID":
            stage_notice(row, workflow, "INPUT_INVALID", self.clock(), changes)
        changes["workflow_json"] = json.dumps(workflow)
        self.repo.update_current(env=row.env, risk_check_id=row.risk_check_id, row_version=row.row_version,
                                 changes=changes)

    def advance(self, env: str, risk_check_id: str) -> None:
        row = self.repo.get(env=env, risk_check_id=risk_check_id)
        if row is None or row.current_target_key is None or row.phase != "ADVANCING" or row.security_status != "PASS":
            return
        workflow = json.loads(row.workflow_json)
        if workflow.get("approval_intent"):
            self._attention(row, "APPROVAL_OUTCOME_UNKNOWN")
            return
        if self.check_h2(row) is not True:
            self._attention(row, "INPUT_OR_PERMISSION_INVALID")
            return
        workflow["approval_intent"] = True
        if not self.repo.update_current(env=env, risk_check_id=risk_check_id, row_version=row.row_version,
                                        changes={"workflow_json": json.dumps(workflow)}):
            return
        row = self.repo.get(env=env, risk_check_id=risk_check_id)
        if row is None or row.current_target_key is None or row.phase != "ADVANCING":
            return

        def on_started(result: dict) -> None:
            puid = result.get("puid")
            if not isinstance(puid, str) or not puid:
                raise ValueError("approval PUID is required")
            for _ in range(3):
                latest = self.repo.get(env=env, risk_check_id=risk_check_id)
                if latest is None or latest.current_target_key is None:
                    raise RuntimeError("risk application no longer current")
                workflow = json.loads(latest.workflow_json)
                if latest.phase == "NEEDS_ATTENTION" and workflow.get("error") == "APPROVAL_OUTCOME_UNKNOWN":
                    if self.check_h2(latest) is not True:
                        raise RuntimeError("risk eligibility changed before approval association")
                    workflow.pop("error", None)
                elif latest.phase != "ADVANCING":
                    raise RuntimeError("risk application no longer advancing")
                if self.repo.update_current(env=env, risk_check_id=risk_check_id, row_version=latest.row_version,
                                            changes={"phase": "APPROVING", "approval_puid": puid,
                                                     "workflow_json": json.dumps(workflow),
                                                     "next_action_at": self.clock() + timedelta(minutes=2)}):
                    return
            raise RuntimeError("approval association could not be saved")

        try:
            outcome = self.submit(row, on_started)
        except Exception:
            latest = self.repo.get(env=env, risk_check_id=risk_check_id)
            if latest is not None and latest.current_target_key is not None and latest.phase == "ADVANCING":
                self._attention(latest, "APPROVAL_OUTCOME_UNKNOWN")
            elif (latest is not None and latest.current_target_key is not None and latest.phase == "APPROVING"
                  and not json.loads(latest.workflow_json).get("approval_target_ready")):
                self._attention(latest, "APPROVAL_TARGET_NOT_READY")
            return
        latest = self.repo.get(env=env, risk_check_id=risk_check_id)
        if latest is None or latest.current_target_key is None:
            return
        if outcome == "approval" and latest.phase == "APPROVING" and latest.approval_puid:
            return
        workflow = json.loads(latest.workflow_json)
        recovered_direct = (latest.phase == "NEEDS_ATTENTION"
                            and workflow.get("error") == "APPROVAL_OUTCOME_UNKNOWN")
        if outcome == "published" and (latest.phase == "ADVANCING" or recovered_direct):
            if recovered_direct and self.check_h2(latest) is not True:
                return
            workflow.pop("error", None)
            self.repo.update_current(env=env, risk_check_id=risk_check_id, row_version=latest.row_version,
                                     changes={"phase": "PUBLISHED", "next_action_at": None,
                                              "workflow_json": json.dumps(workflow)})
            return
        if latest.phase == "ADVANCING":
            self._attention(latest, "APPROVAL_OUTCOME_UNKNOWN")


class RiskAdvanceHandler:
    task_type = "bot_public_risk_advance"

    def __init__(self, advance: RiskAdvance, env_provider: Callable[[], str]):
        self.advance, self.env_provider = advance, env_provider

    def handle(self, payload: dict | None):
        from agentclaw.community.core.task_queue.types import Complete
        from agentclaw.community.utils.avernet_tenant import avernet_tenant_scope

        if not isinstance(payload, dict):
            raise ValueError("invalid advance task")
        tenant, env, risk_id = (payload.get(key) for key in ("tenant", "env", "risk_check_id"))
        if not all(isinstance(value, str) and value and value.strip() == value for value in (tenant, env, risk_id)):
            raise ValueError("invalid advance identity")
        if env != self.env_provider():
            raise ValueError("advance environment differs from worker")
        with avernet_tenant_scope(tenant):
            self.advance.advance(env, risk_id)
        return Complete()


class RiskPendingHandler:
    task_type = "bot_public_risk_callback_replay"

    def __init__(self, repository, bots, public_service, guard, env_provider, clock=None):
        self.repo, self.bots, self.public_service, self.guard, self.env_provider = (
            repository, bots, public_service, guard, env_provider
        )
        self.clock = clock or (lambda: datetime.now(timezone.utc).replace(tzinfo=None))

    def handle(self, payload: dict | None):
        from agentclaw.community.core.task_queue.types import Complete, Reschedule
        from agentclaw.community.utils.avernet_tenant import avernet_tenant_scope

        if not isinstance(payload, dict):
            raise ValueError("invalid callback replay task")
        tenant, env, risk_id = (payload.get(key) for key in ("tenant", "env", "risk_check_id"))
        if not all(isinstance(value, str) and value and value.strip() == value for value in (tenant, env, risk_id)):
            raise ValueError("invalid callback replay identity")
        if env != self.env_provider():
            raise ValueError("callback replay environment differs from worker")
        with avernet_tenant_scope(tenant):
            row = self.repo.get(env=env, risk_check_id=risk_id)
            if row is None or row.current_target_key is None or row.phase != "APPROVING":
                return Complete()
            workflow = json.loads(row.workflow_json)
            if workflow.get("approval_target_ready") is not True:
                if row.next_action_at is not None and row.next_action_at > self.clock():
                    return Reschedule(delay_seconds=(row.next_action_at - self.clock()).total_seconds())
                workflow["error"] = "APPROVAL_TARGET_NOT_READY"
                self.repo.update_current(env=env, risk_check_id=risk_id, row_version=row.row_version,
                                         changes={"phase": "NEEDS_ATTENTION", "next_action_at": None,
                                                  "workflow_json": json.dumps(workflow)})
                return Complete()
            if (workflow.get("callback_claim") or {}).get("status") == "processing":
                if row.next_action_at is not None and row.next_action_at > self.clock():
                    return Reschedule(delay_seconds=(row.next_action_at - self.clock()).total_seconds())
                workflow["error"] = "CALLBACK_OUTCOME_UNKNOWN"
                self.repo.update_current(env=env, risk_check_id=risk_id, row_version=row.row_version,
                                         changes={"phase": "NEEDS_ATTENTION", "next_action_at": None,
                                                  "workflow_json": json.dumps(workflow)})
                return Complete()
            bot = self.bots.get_by_pk(row.bot_pk)
            if not bot:
                raise LookupError("risk bot unavailable during callback replay")
            bot_id = bot["bot_id"] if row.public_scope == "legacy" else f"{bot['bot_id']}:{bot['entity_id']}"
            scope = None if row.public_scope == "legacy" else row.public_scope
            replayed = self.guard.replay(env, risk_id, lambda puid, decision:
                self.public_service.handle_public_approval_callback(
                    bot_id=bot_id, owner_id=row.owner_id, puid=puid, last_operate=decision,
                    public_scope=scope, risk_check_id=risk_id,
                ))
            if not replayed:
                current = self.repo.get(env=env, risk_check_id=risk_id)
                if current is not None and current.current_target_key is not None and current.phase == "APPROVING":
                    workflow = json.loads(current.workflow_json)
                    if ((workflow.get("callback_claim") or {}).get("status") == "processing"
                            and current.next_action_at is not None and current.next_action_at > self.clock()):
                        return Reschedule(delay_seconds=(current.next_action_at - self.clock()).total_seconds())
                    if workflow.get("pending_approval"):
                        workflow["error"] = "CALLBACK_REPLAY_BLOCKED"
                        self.repo.update_current(env=env, risk_check_id=risk_id, row_version=current.row_version,
                                                 changes={"phase": "NEEDS_ATTENTION", "next_action_at": None,
                                                          "workflow_json": json.dumps(workflow)})
        return Complete()
