"""Governance feedback service — task_record based (§7.4).

Handles 4 formal responses (optimized / need_time / dispute / whitelist)
via the **one-time feedback rule** (§7.4.1): one task_record gets at most
one user response. Repeated clicks or terminal-state submissions are
rejected with audit but no field mutation.

All lifecycle transitions land on ``task_record`` — never on ``notify_log``
(§4.2.3 读写路由规则). User feedback enters ``waiting_review`` (Phase1
rule, §7.4.2) rather than closing the ticket directly; all closures are
admin-driven (§7.5).
"""
from __future__ import annotations

import json
from agentclaw.community.log import get_logger
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from injector import inject

from agentclaw.community.core.economy.governance.domain.enums import (
    AuditAction,
    GovernanceStatus,
    Response,
)


if TYPE_CHECKING:
    from agentclaw.community.core.economy.governance.repositories.audit_repo import (
        GovernanceAuditRepository,
    )
    from agentclaw.community.core.economy.governance.repositories.notify_log_repo import (
        NotifyLogRepository,
    )
    from agentclaw.community.core.economy.governance.repositories.task_record_repo import (
        TaskRecordRepository,
    )
    from agentclaw.community.core.economy.governance.services.lifecycle_service import (
        GovernanceLifecycleService,
    )
    from agentclaw.community.core.economy.governance.services.whitelist_service import (
        GovernanceWhitelistService,
    )

log = get_logger(__name__)

# Valid formal responses
_FORMAL_RESPONSES = {e.value for e in Response}

# Statuses that block user feedback (§7.4.1 step 3)
_BLOCKED_STATUSES = {GovernanceStatus.SCHEDULED, GovernanceStatus.WAITING_REVIEW, GovernanceStatus.CLOSED}

# response → (target_status, review_reason) — all go to waiting_review (§7.4.2)
_RESPONSE_TRANSITION_MAP: dict[str, tuple[str, str]] = {
    Response.OPTIMIZED: (GovernanceStatus.WAITING_REVIEW, "user_optimized"),
    Response.DISPUTE: (GovernanceStatus.WAITING_REVIEW, "user_disputed"),
    Response.WHITELIST: (GovernanceStatus.WAITING_REVIEW, "user_whitelisted"),
    # need_time → scheduled, handled separately
}


@dataclass
class ResolveResult:
    """Result of a resolve operation."""

    success: bool = False
    ticket_id: str = ""
    governance_status: str = ""
    close_reason: str | None = None
    mute_until: datetime | None = None
    error: str | None = None
    # Card callback needs these fields for response body
    response: str = ""
    response_source: str = ""
    message: str | None = None
    # Notification ID for backward compat (card callback traceback)
    notification_id: str = ""
    # Structured error code for HTTP status mapping
    error_code: str | None = None


def _result_from_ticket(
    ticket: Any,
    *,
    notification_id: str = "",
    message: str | None = None,
) -> ResolveResult:
    """Build a ResolveResult from a GovernanceTicket for idempotent returns."""
    return ResolveResult(
        success=True,
        ticket_id=(ticket.ticket_id or ""),
        notification_id=notification_id,
        governance_status=(ticket.governance_status or ""),
        close_reason=ticket.close_reason,
        mute_until=ticket.resume_at,
        response=(ticket.user_feedback or ""),
        response_source=(ticket.feedback_source or ""),
        message=message,
    )


class GovernanceFeedbackService:
    """Handle user feedback on governance tickets (§7.4)."""

    @inject
    def __init__(
        self,
        whitelist_service: GovernanceWhitelistService,
        notify_repo: NotifyLogRepository,
        audit_repo: GovernanceAuditRepository,
        task_repo: TaskRecordRepository,
        config: Any,  # EconomyGovernanceConfig
        lifecycle_svc: GovernanceLifecycleService,
    ) -> None:
        # ``whitelist_service`` / ``notify_repo`` retained as injected deps
        # (constructor signature stable across migration); the resolve path
        # now delegates whitelist-add + cancel-pending to lifecycle_svc, so
        # these are read only by future admin/review paths. Group C cleanup
        # may drop them if confirmed unused.
        self._whitelist_service = whitelist_service
        self._notify_repo = notify_repo
        self._audit_repo = audit_repo
        self._task_repo = task_repo
        self._config = config
        self._lifecycle_svc = lifecycle_svc

    def resolve(
        self,
        notification_id: str,
        response: str,
        user_id: str = "",
        *,
        actor_id: str | None = None,
        remark: str | None = None,
        source: str = "http_api",
        repair_deadline: datetime | None = None,
        feedback_payload: dict | None = None,
    ) -> ResolveResult:
        """Process a user response on a governance notification (§7.4).

        One-time feedback rule (§7.4.1):
          1. Ticket not found → error
          2. response not empty → duplicate ignored
          3. status in (scheduled, waiting_review, closed) → terminal ignored
          4. Only open + response empty → accept

        State transitions (§7.4.2):
          - optimized/dispute/whitelist → waiting_review
          - need_time → scheduled

        Args:
            notification_id: The notification to resolve.
            response: One of optimized/need_time/dispute/whitelist.
            user_id: The user's ID.  When empty (card_callback),
                owner_id is resolved from the DB record.
            actor_id: Actual operator (defaults to user_id).
            remark: Optional remark (required for dispute and whitelist).
            source: Response source (http_api / card_callback / admin_api).
            repair_deadline: Required for need_time.
            feedback_payload: Optional structured feedback JSON.

        Returns:
            ResolveResult with outcome details.
        """
        # Find ticket via notification_id → notify_log.ticket_id → task_record
        # (§7.4.1 step 1) — repo uses self-managed session
        ticket = self._task_repo.find_ticket_by_notification_id(
            notification_id,
        )
        if not ticket:
            return ResolveResult(
                success=False,
                error="该治理工单不存在或已失效",
                error_code="NOT_FOUND",
                notification_id=notification_id,
            )

        # Resolve effective user ID from DB if empty (card_callback)
        effective_user_id = user_id or ticket.owner_id or ""
        effective_actor = actor_id or effective_user_id

        # §7.4.1 step 2: response not empty → duplicate ignored
        if ticket.user_feedback is not None and ticket.user_feedback != "":
            self._audit_repo.add_audit(
                f"feedback-{uuid.uuid4().hex[:8]}",
                ticket.bot_id,
                ticket.owner_id,
                notification_id=notification_id,
                actor_id=effective_user_id,
                action_taken=AuditAction.FEEDBACK_DUPLICATE_IGNORED,
                source=source,
                dry_run=0,
            )
            return _result_from_ticket(
                ticket,
                notification_id=notification_id,
                message="该治理工单已反馈过，无需重复提交",
            )

        # §7.4.1 step 3: terminal status → rejected
        if ticket.governance_status in _BLOCKED_STATUSES:
            self._audit_repo.add_audit(
                f"feedback-{uuid.uuid4().hex[:8]}",
                ticket.bot_id,
                ticket.owner_id,
                notification_id=notification_id,
                actor_id=effective_user_id,
                action_taken=AuditAction.FEEDBACK_TERMINAL_IGNORED,
                source=source,
                error_msg=f"status={ticket.governance_status}",
                dry_run=0,
            )
            return ResolveResult(
                success=False,
                error="该治理工单状态不允许反馈",
                error_code="INVALID_STATUS",
                ticket_id=(ticket.ticket_id or ""),
                notification_id=notification_id,
            )

        # §7.4.1 step 4: must be open + response empty
        # (scheduled/waiting_review already filtered above)
        if ticket.governance_status != GovernanceStatus.OPEN:
            return ResolveResult(
                success=False,
                error=f"Unexpected status: {ticket.governance_status}",
                error_code="INVALID_STATUS",
                ticket_id=(ticket.ticket_id or ""),
                notification_id=notification_id,
            )

        # Validate response value
        if response not in _FORMAL_RESPONSES:
            return ResolveResult(
                success=False,
                error=f"Invalid response: {response}",
                error_code="INVALID_RESPONSE",
                notification_id=notification_id,
            )

        # Dispute/whitelist require remark
        if response in (Response.DISPUTE, Response.WHITELIST) and not remark:
            return ResolveResult(
                success=False,
                error="Remark is required for dispute/whitelist",
                error_code="MISSING_REMARK",
                notification_id=notification_id,
            )

        # Need_time requires repair_deadline
        if response == Response.NEED_TIME and not repair_deadline:
            return ResolveResult(
                success=False,
                error="repair_deadline is required for need_time",
                error_code="MISSING_REPAIR_DEADLINE",
                notification_id=notification_id,
            )

        # Apply feedback (§7.4.2)
        now = datetime.now()
        target_status: str
        review_reason: str | None = None
        mute_until: datetime | None = None

        if response == Response.NEED_TIME:
            target_status = GovernanceStatus.SCHEDULED
            mute_until = repair_deadline + timedelta(
                days=self._config.cooldown_days,
            )
        else:
            target_status, review_reason = _RESPONSE_TRANSITION_MAP[response]

        # Validate feedback_payload
        feedback_payload_json: str | None = None
        if feedback_payload is not None:
            try:
                json.dumps(feedback_payload)
                feedback_payload_json = json.dumps(feedback_payload)
            except (TypeError, ValueError):
                return ResolveResult(
                    success=False,
                    error="Invalid feedback_payload JSON",
                    error_code="INVALID_FEEDBACK_PAYLOAD",
                    notification_id=notification_id,
                )

        # Advance ticket state via the driver service (sole driver). The
        # driver orchestrates: state transition (guard-activated) + cancel
        # pending notifies. Pre-business checks (one-time rule, response
        # validity, remark/deadline requirements) stay here in feedback_service.
        updated = self._lifecycle_svc.accept_feedback(
            ticket.ticket_id,
            user_feedback=response,
            feedback_at=now,
            feedback_source=source,
            target_status=target_status,
            feedback_remark=remark,
            repair_deadline=repair_deadline if response == Response.NEED_TIME else None,
            resume_at=mute_until if response == Response.NEED_TIME else None,
            review_reason=review_reason if response != Response.NEED_TIME else None,
            actor_id=effective_actor,
            feedback_payload=feedback_payload_json,
        )
        if not updated:
            return ResolveResult(
                success=False,
                error="该治理工单不存在或已失效",
                error_code="NOT_FOUND",
                notification_id=notification_id,
            )

        # Whitelist feedback → add to the whitelist table. Owned by
        # feedback_service (not the driver) to keep lifecycle_service free of
        # a whitelist_service dependency (breaks the whitelist↔lifecycle DI
        # cycle). Source & created_by carry the rich feedback semantics
        # (effective_user_id = owner; original source e.g. card_callback).
        if response == Response.WHITELIST:
            try:
                self._whitelist_service.add(
                    bot_id=ticket.bot_id,
                    owner_id=ticket.owner_id,
                    created_by=effective_user_id,
                    whitelist_type="governance",
                    source=source,
                )
            except Exception:
                log.exception(
                    "[GovernanceFeedback] Failed to add whitelist for bot_id=%s",
                    ticket.bot_id,
                )

        # Audit (§7.4.3) — feedback_service keeps its per-response audit
        # (USER_OPTIMIZED / USER_NEED_TIME / USER_DISPUTE / USER_WHITELIST),
        # which the driver does not duplicate.
        _RESPONSE_AUDIT_MAP: dict[str, str] = {
            Response.OPTIMIZED: AuditAction.USER_OPTIMIZED,
            Response.NEED_TIME: AuditAction.USER_NEED_TIME,
            Response.DISPUTE: AuditAction.USER_DISPUTE,
            Response.WHITELIST: AuditAction.USER_WHITELIST,
        }
        audit_action = _RESPONSE_AUDIT_MAP.get(response, response)
        self._audit_repo.add_audit(
            f"feedback-{uuid.uuid4().hex[:8]}",
            ticket.bot_id,
            ticket.owner_id,
            notification_id=notification_id,
            actor_id=effective_user_id,
            check_result="actionable",
            action_taken=audit_action,
            source=source,
            dry_run=0,
        )

        return ResolveResult(
            success=True,
            ticket_id=(ticket.ticket_id or ""),
            notification_id=notification_id,
            governance_status=target_status,
            close_reason=ticket.close_reason,
            mute_until=mute_until if response == Response.NEED_TIME else None,
            response=response,
            response_source=source,
        )

    # ------------------------------------------------------------------
    # List queries (list_pending/list_history/get_notification) 已删除:
    # 无真实用户主动调用,    治理反馈真入口是 card-callback(经 resolve)。完整移除于
    # admin-router-regroup Task 7。仅保留 resolve。
    # ------------------------------------------------------------------

    