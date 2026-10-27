"""Durable security submission and original-report polling.

The provider interprets the external contract; this layer never infers PASS
from timeout, an absent field, or an unverified default.
"""

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from hashlib import sha256
import json
import logging
from typing import Callable, Protocol

from agentclaw.community.core.bot_public_risk.repository import (
    BotPublicRiskRepository,
    RiskCheck,
)
from agentclaw.community.core.bot_public_risk.notices import _next_due, stage_notice
from agentclaw.community.core.task_queue.types import Complete, Reschedule, Retry
from agentclaw.community.utils.env_utils import get_current_env
from agentclaw.community.utils.avernet_tenant import (
    avernet_tenant_scope,
    get_current_avernet_tenant,
)


@dataclass(frozen=True)
class SecurityReport:
    status: str
    detail_url: str | None = None
    valid_pass: bool = False


class SecurityProviderUnavailable(RuntimeError):
    """No external submission was attempted because no provider is configured."""


class SecurityProvider(Protocol):
    def submit_agent_scan(self, agent_info: dict) -> str: ...
    def get_agent_check_report(self, check_id: str) -> SecurityReport: ...


SUBMIT = "bot_public_risk_submit"
DETECTING = "bot_public_risk_detecting"
REJECT = "bot_public_risk_reject"
ADVANCE = "bot_public_risk_advance"
REPLAY = "bot_public_risk_callback_replay"
NOTICE = "bot_public_risk_notice"
logger = logging.getLogger(__name__)


class RiskTasks:
    def __init__(
        self,
        repository: BotPublicRiskRepository,
        queue,
        provider: SecurityProvider,
        clock: Callable[[], datetime],
    ) -> None:
        self.repo, self.queue, self.provider, self.clock = (
            repository,
            queue,
            provider,
            clock,
        )

    def _enqueue(self, record: RiskCheck, kind: str, delay: int = 0) -> None:
        tenant = get_current_avernet_tenant()
        key = sha256(
            f"{tenant}\0{record.env}\0{record.risk_check_id}\0{kind}".encode()
        ).hexdigest()
        self.queue.enqueue(
            kind,
            {
                "tenant": tenant,
                "env": record.env,
                "risk_check_id": record.risk_check_id,
            },
            deadline_seconds=7200,
            delay_seconds=delay,
            idempotency_key=f"risk:{key}",
        )

    def start(self, record: RiskCheck) -> tuple[RiskCheck, bool]:
        saved, created = self.repo.create_or_replay(
            replace(record, next_action_at=self.clock())
        )
        if created:
            self._enqueue(saved, SUBMIT)
        return saved, created

    def _update(self, row: RiskCheck, changes: dict) -> bool:
        workflow = json.loads(changes.get("workflow_json", row.workflow_json))
        if changes.get("security_status") == "REJECT":
            stage_notice(row, workflow, "REJECT", self.clock(), changes)
        if changes.get("auto_poll_paused") is True and not row.auto_poll_paused:
            stage_notice(row, workflow, "WINDOW_ENDED", self.clock(), changes)
        if workflow.get("error") == "SUBMISSION_OUTCOME_UNKNOWN":
            stage_notice(row, workflow, "CREATION_UNKNOWN", self.clock(), changes)
        if workflow.get("error") == "SECURITY_PROVIDER_UNAVAILABLE":
            stage_notice(row, workflow, "CREATION_FAILED", self.clock(), changes)
        if "notification_due_at" in changes:
            changes["workflow_json"] = json.dumps(workflow)
        updated = self.repo.update_current(
            env=row.env,
            risk_check_id=row.risk_check_id,
            row_version=row.row_version,
            changes=changes,
        )
        if updated and "notification_due_at" in changes:
            try:
                self._enqueue(row, NOTICE)
            except Exception:
                # Durable due timestamp remains for the recovery scanner.
                logger.warning(
                    "risk notice enqueue failed; recovery will retry", exc_info=True
                )
        return updated

    def recover_notices(
        self, *, env: str, now: datetime, after_id: str = "", limit: int = 100
    ) -> str | None:
        rows = self.repo.list_notice_due(
            env=env, now=now, after_id=after_id, limit=limit
        )
        for row in rows:
            self._enqueue(row, NOTICE)
        return rows[-1].risk_check_id if len(rows) == limit else None

    def submit(self, *, env: str, risk_check_id: str) -> None:
        row = self.repo.get(env=env, risk_check_id=risk_check_id)
        if row is None or row.current_target_key is None or row.phase != "PREPARING":
            return
        workflow = json.loads(row.workflow_json)
        if workflow.get("submission_intent"):
            # A reclaimed task cannot prove whether the first POST succeeded.
            workflow["error"] = "SUBMISSION_OUTCOME_UNKNOWN"
            self._update(
                row,
                {
                    "phase": "NEEDS_ATTENTION",
                    "next_action_at": None,
                    "workflow_json": json.dumps(workflow),
                },
            )
            return
        workflow["submission_intent"] = True
        if not self._update(row, {"workflow_json": json.dumps(workflow)}):
            return
        row = self.repo.get(env=env, risk_check_id=risk_check_id)
        if row is None or row.current_target_key is None or row.phase != "PREPARING":
            return
        try:
            check_id = self.provider.submit_agent_scan(
                json.loads(row.input_json)["agent_info"]
            )
            if not isinstance(check_id, str) or not check_id.strip():
                raise ValueError("missing check id")
        except SecurityProviderUnavailable:
            workflow["error"] = "SECURITY_PROVIDER_UNAVAILABLE"
            self._update(
                row,
                {
                    "phase": "NEEDS_ATTENTION",
                    "next_action_at": None,
                    "workflow_json": json.dumps(workflow),
                },
            )
            return
        except Exception:
            workflow["error"] = "SUBMISSION_OUTCOME_UNKNOWN"
            self._update(
                row,
                {
                    "phase": "NEEDS_ATTENTION",
                    "next_action_at": None,
                    "workflow_json": json.dumps(workflow),
                },
            )
            return
        self._record_submission_result(
            env=env, risk_check_id=risk_check_id, check_id=check_id
        )

    def _record_submission_result(
        self, *, env: str, risk_check_id: str, check_id: str
    ) -> None:
        # A reclaimed lease may already have marked this same attempt unknown.
        # A real response is stronger evidence; retain it without repeating POST.
        # Do not revive a replaced/withdrawn application or overwrite another ID.
        for _ in range(3):
            row = self.repo.get(env=env, risk_check_id=risk_check_id)
            if (
                row is None
                or row.current_target_key is None
                or row.check_id is not None
            ):
                return
            workflow = json.loads(row.workflow_json)
            if workflow.get("submission_intent") is not True:
                return
            if row.phase == "NEEDS_ATTENTION":
                if workflow.get("error") != "SUBMISSION_OUTCOME_UNKNOWN":
                    return
                del workflow["error"]
            elif row.phase != "PREPARING":
                return
            now = self.clock()
            notices = workflow.get("notices", {})
            unknown = notices.get("CREATION_UNKNOWN")
            if unknown and unknown["status"] == "pending":
                del notices["CREATION_UNKNOWN"]
            # A sender already in flight may have delivered. Keep that honest
            # history, but do not reclaim or retry it after the ID is known.
            due_notices = {k: v for k, v in notices.items() if k != "CREATION_UNKNOWN"}
            workflow["detecting_deadline"] = (now + timedelta(minutes=5)).isoformat()
            notice_changes = (
                {"notification_due_at": _next_due(due_notices)}
                if unknown and unknown["status"] != "sent"
                else {}
            )
            if self._update(
                row,
                {
                    **notice_changes,
                    "workflow_json": json.dumps(workflow),
                    "phase": "CHECKING",
                    "security_status": "DETECTING",
                    "check_id": check_id,
                    "next_action_at": now + timedelta(seconds=30),
                    "auto_poll_until": now + timedelta(minutes=5),
                },
            ):
                self._enqueue(row, DETECTING, 30)
                return
        # The returned ID must not be silently dropped on repeated contention.
        raise RuntimeError("could not persist security check result after contention")

    def poll(self, *, env: str, risk_check_id: str, manual: bool = False):
        row = self.repo.get(env=env, risk_check_id=risk_check_id)
        if (
            row is None
            or row.current_target_key is None
            or row.phase != "CHECKING"
            or not row.check_id
        ):
            return "NOT_QUERIED"
        now = self.clock()
        if not manual and (
            row.auto_poll_paused
            or (row.auto_poll_until is not None and now >= row.auto_poll_until)
        ):
            if not row.auto_poll_paused:
                self._update(row, {"auto_poll_paused": True, "next_action_at": None})
            return "NOT_QUERIED"
        if not manual and row.next_action_at is not None and now < row.next_action_at:
            return "NOT_QUERIED"
        report = self.provider.get_agent_check_report(row.check_id)
        if report.status not in {"DETECTING", "REJECT", "PASS"}:
            raise ValueError("unknown security report status")
        workflow = json.loads(row.workflow_json)
        changes = {"security_status": report.status, "detail_url": report.detail_url}
        if report.status == "PASS":
            changes.update(
                {
                    "phase": "ADVANCING"
                    if report.valid_pass is True
                    else "NEEDS_ATTENTION",
                    "next_action_at": now if report.valid_pass is True else None,
                    "auto_poll_paused": False,
                }
            )
            if report.valid_pass is not True:
                workflow["error"] = "PASS_UNVERIFIED"
        else:
            # The two windows belong to different events. A return from
            # REJECT to DETECTING must never inherit the longer window.
            detection_deadline = workflow.get("detecting_deadline")
            deadline = (
                datetime.fromisoformat(detection_deadline)
                if detection_deadline
                else None
            )
            if report.status == "REJECT":
                if not workflow.get("reject_started_at"):
                    workflow["reject_started_at"] = now.isoformat()
                deadline = datetime.fromisoformat(
                    workflow["reject_started_at"]
                ) + timedelta(hours=1)
            interval = 300 if report.status == "REJECT" else 30
            paused = deadline is None or now >= deadline
            changes.update(
                {
                    "auto_poll_until": deadline,
                    "auto_poll_paused": paused,
                    "next_action_at": None
                    if paused
                    else min(now + timedelta(seconds=interval), deadline),
                }
            )
        changes["workflow_json"] = json.dumps(workflow)
        if not self._update(row, changes):
            return "UPDATED"
        current = self.repo.get(env=env, risk_check_id=risk_check_id)
        if current is not None and current.current_target_key is not None:
            if current.phase == "ADVANCING":
                self._enqueue(current, ADVANCE)
            elif (
                manual and current.phase == "CHECKING" and not current.auto_poll_paused
            ):
                delay = max(0, int((current.next_action_at - now).total_seconds()))
                self._enqueue(
                    current, REJECT if report.status == "REJECT" else DETECTING, delay
                )
        return "UPDATED"

    def recover(
        self, *, env: str, now: datetime, after_id: str = "", limit: int = 100
    ) -> str | None:
        rows = self.repo.list_due(env=env, now=now, after_id=after_id, limit=limit)
        for row in rows:
            if row.phase == "PREPARING":
                self._enqueue(row, SUBMIT)
            elif row.phase == "CHECKING":
                self._enqueue(
                    row, REJECT if row.security_status == "REJECT" else DETECTING
                )
            elif row.phase == "ADVANCING":
                self._enqueue(row, ADVANCE)
            elif row.phase == "APPROVING":
                workflow = json.loads(row.workflow_json)
                if workflow.get("pending_approval") or (workflow.get("callback_claim") or {}).get("status") == "processing":
                    self._enqueue(row, REPLAY)
        return rows[-1].risk_check_id if len(rows) == limit else None


class RiskTaskHandler:
    def __init__(
        self,
        tasks: RiskTasks,
        kind: str,
        env_provider: Callable[[], str] = get_current_env,
    ) -> None:
        if kind not in {SUBMIT, DETECTING, REJECT}:
            raise ValueError("invalid task kind")
        self.tasks, self.kind, self.env_provider = tasks, kind, env_provider

    @property
    def task_type(self) -> str:
        return self.kind

    def handle(self, payload: dict | None):
        if not isinstance(payload, dict):
            raise ValueError("risk task payload must be an object")
        tenant, env, risk_check_id = (
            payload.get(k) for k in ("tenant", "env", "risk_check_id")
        )
        if not all(
            isinstance(v, str) and v and v.strip() == v
            for v in (tenant, env, risk_check_id)
        ):
            raise ValueError("invalid risk task identity")
        if env != self.env_provider():
            raise ValueError("risk task environment differs from worker")
        with avernet_tenant_scope(tenant):
            args = {"env": env, "risk_check_id": risk_check_id}
            if self.kind == SUBMIT:
                self.tasks.submit(**args)
                return Complete()
            try:
                self.tasks.poll(**args)
            except Exception as exc:
                return Retry(error=type(exc).__name__)
            row = self.tasks.repo.get(**args)
            if (
                row is None
                or row.current_target_key is None
                or row.phase != "CHECKING"
                or row.auto_poll_paused
            ):
                return Complete()
            new_kind = REJECT if row.security_status == "REJECT" else DETECTING
            delay = max(
                0, int((row.next_action_at - self.tasks.clock()).total_seconds())
            )
            if new_kind != self.kind:
                self.tasks._enqueue(row, new_kind, delay)
                return Complete()
            return Reschedule(delay_seconds=delay)
