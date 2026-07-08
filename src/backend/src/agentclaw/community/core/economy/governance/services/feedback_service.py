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
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from injector import inject

from agentclaw.community.core.economy.governance.contracts.enums import AuditAction
from agentclaw.community.core.economy.governance.contracts.models import (
    GovernanceTaskRecordDaily,
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
    from agentclaw.community.core.economy.governance.services.whitelist_service import (
        GovernanceWhitelistService,
    )
    from agentclaw.community.plugin_api.database_protocol import DatabasePlugin

log = logging.getLogger(__name__)

# Valid formal responses
_FORMAL_RESPONSES = {"optimized", "need_time", "dispute", "whitelist"}

# Statuses that block user feedback (§7.4.1 step 3)
_BLOCKED_STATUSES = {"scheduled", "waiting_review", "closed"}

# response → (target_status, review_reason) — all go to waiting_review (§7.4.2)
_RESPONSE_TRANSITION_MAP: dict[str, tuple[str, str]] = {
    "optimized": ("waiting_review", "user_optimized"),
    "dispute": ("waiting_review", "user_disputed"),
    "whitelist": ("waiting_review", "user_whitelisted"),
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
    ticket: dict,
    *,
    notification_id: str = "",
    message: str | None = None,
) -> ResolveResult:
    """Build a ResolveResult from a ticket row for idempotent returns."""
    return ResolveResult(
        success=True,
        ticket_id=ticket.get("ticket_id") or "",
        notification_id=notification_id,
        governance_status=ticket.get("governance_status") or "",
        close_reason=ticket.get("close_reason"),
        mute_until=ticket.get("mute_until"),
        response=ticket.get("response") or "",
        response_source=ticket.get("response_source") or "",
        message=message,
    )


class GovernanceFeedbackService:
    """Handle user feedback on governance tickets (§7.4)."""

    @inject
    def __init__(
        self,
        db: DatabasePlugin,
        whitelist_service: GovernanceWhitelistService,
        notify_repo: NotifyLogRepository,
        audit_repo: GovernanceAuditRepository,
        task_repo: TaskRecordRepository,
        config: Any,  # EconomyGovernanceConfig
    ) -> None:
        self._db = db
        self._whitelist_service = whitelist_service
        self._notify_repo = notify_repo
        self._audit_repo = audit_repo
        self._task_repo = task_repo
        self._config = config

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
        effective_user_id = user_id or ticket.get("owner_id") or ""
        effective_actor = actor_id or effective_user_id

        # §7.4.1 step 2: response not empty → duplicate ignored
        if ticket.get("response") is not None and ticket.get("response") != "":
            self._audit_repo.add_audit(
                f"feedback-{uuid.uuid4().hex[:8]}",
                ticket.get("bot_id"),
                ticket.get("owner_id"),
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
        if ticket.get("governance_status") in _BLOCKED_STATUSES:
            self._audit_repo.add_audit(
                f"feedback-{uuid.uuid4().hex[:8]}",
                ticket.get("bot_id"),
                ticket.get("owner_id"),
                notification_id=notification_id,
                actor_id=effective_user_id,
                action_taken=AuditAction.FEEDBACK_TERMINAL_IGNORED,
                source=source,
                error_msg=f"status={ticket.get('governance_status')}",
                dry_run=0,
            )
            return ResolveResult(
                success=False,
                error="该治理工单状态不允许反馈",
                error_code="INVALID_STATUS",
                ticket_id=ticket.get("ticket_id") or "",
                notification_id=notification_id,
            )

        # §7.4.1 step 4: must be open + response empty
        # (scheduled/waiting_review already filtered above)
        if ticket.get("governance_status") != "open":
            return ResolveResult(
                success=False,
                error=f"Unexpected status: {ticket.get('governance_status')}",
                error_code="INVALID_STATUS",
                ticket_id=ticket.get("ticket_id") or "",
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
        if response in ("dispute", "whitelist") and not remark:
            return ResolveResult(
                success=False,
                error="Remark is required for dispute/whitelist",
                error_code="MISSING_REMARK",
                notification_id=notification_id,
            )

        # Need_time requires repair_deadline
        if response == "need_time" and not repair_deadline:
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

        if response == "need_time":
            target_status = "scheduled"
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

        # Update ticket via managed session
        with self._db.orm_session() as s:
            db_ticket = (
                s.query(GovernanceTaskRecordDaily)
                .filter(
                    GovernanceTaskRecordDaily.ticket_id == ticket.get("ticket_id"),
                )
                .one_or_none()
            )
            if db_ticket is None:
                return ResolveResult(
                    success=False,
                    error="该治理工单不存在或已失效",
                    error_code="NOT_FOUND",
                    notification_id=notification_id,
                )

            db_ticket.response = response
            db_ticket.response_at = now
            db_ticket.response_remark = remark
            db_ticket.response_source = source
            db_ticket.actor_id = effective_actor
            db_ticket.remind_at = None  # Stop reminder chain on any feedback
            db_ticket.governance_status = target_status

            if feedback_payload_json is not None:
                db_ticket.feedback_payload = feedback_payload_json

            if response == "need_time":
                db_ticket.repair_deadline = repair_deadline
                db_ticket.mute_until = mute_until
            else:
                db_ticket.review_reason = review_reason

            try:
                s.commit()
            except Exception:
                log.exception("[GovernanceFeedback] Resolve commit failed")
                s.rollback()
                return ResolveResult(
                    success=False,
                    error="Database error",
                    error_code="DB_ERROR",
                    notification_id=notification_id,
                )

        # Cancel pending notifies (§7.4.2 footnote) — self-managed session
        if ticket.get("ticket_id"):
            self._notify_repo.cancel_pending_by_ticket(
                ticket.get("ticket_id"),
            )

        # Whitelist → also add to whitelist table
        if response == "whitelist":
            try:
                self._whitelist_service.batch_add(
                    entries=[{"bot_id": ticket.get("bot_id"), "owner_id": ticket.get("owner_id")}],
                    created_by=effective_user_id,
                    whitelist_type="governance",
                    source=source,
                )
            except Exception:
                log.exception(
                    "[GovernanceFeedback] Failed to add whitelist for bot_id=%s",
                    ticket.get("bot_id"),
                )

        # Audit (§7.4.3)
        _RESPONSE_AUDIT_MAP: dict[str, str] = {
            "optimized": AuditAction.USER_OPTIMIZED,
            "need_time": AuditAction.USER_NEED_TIME,
            "dispute": AuditAction.USER_DISPUTE,
            "whitelist": AuditAction.USER_WHITELIST,
        }
        audit_action = _RESPONSE_AUDIT_MAP.get(response, response)
        self._audit_repo.add_audit(
            f"feedback-{uuid.uuid4().hex[:8]}",
            ticket.get("bot_id"),
            ticket.get("owner_id"),
            notification_id=notification_id,
            actor_id=effective_user_id,
            check_result="actionable",
            action_taken=audit_action,
            source=source,
            dry_run=0,
        )

        return ResolveResult(
            success=True,
            ticket_id=ticket.get("ticket_id") or "",
            notification_id=notification_id,
            governance_status=target_status,
            close_reason=ticket.get("close_reason"),
            mute_until=mute_until if response == "need_time" else None,
            response=response,
            response_source=source,
        )

    # ------------------------------------------------------------------
    # List queries (task_record based)
    # ------------------------------------------------------------------

    def list_pending(
        self,
        owner_id: str,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict]:
        """List pending (open/scheduled) tickets for a user."""
        return self._task_repo.list_tickets_by_owner_and_statuses(
            owner_id, ["open", "scheduled"],
            offset=offset, limit=limit,
        )

    def list_history(
        self,
        owner_id: str,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict]:
        """List closed tickets for a user."""
        return self._task_repo.list_tickets_by_owner_and_statuses(
            owner_id, ["closed"],
            offset=offset, limit=limit,
        )

    def get_notification(
        self,
        notification_id: str,
        owner_id: str,
    ) -> dict | None:
        """Get a single ticket by notification_id (owner check)."""
        ticket = self._task_repo.find_ticket_by_notification_id(
            notification_id,
        )
        if ticket is None or ticket.get("owner_id") != owner_id:
            return None
        return ticket

    