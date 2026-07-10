"""Governance admin service — backend management operations (§7.5).

Covers:
  - Emergency brake (pause/resume) — cross-pod distributed cache
  - bulk_whitelist — delegate to :class:`GovernanceWhitelistService`
  - cancel_pending / close_all_open — emergency bulk operations
  - pause_ticket — admin pause to waiting_review (§7.5.1)
  - review_ticket — admin review: approve_close / approve_whitelist /
    reject_for_reopen (§7.5.2)
  - emergency_close — immediate ticket close without cooldown
  - delete_records — emergency delete for record_daily / notify_log
  - delete_whitelist_entry — delegate to :class:`GovernanceWhitelistService`
  - deliver_pending — scan-and-deliver pipeline (testing tool)

All ticket lifecycle transitions land on ``task_record`` — never on
``notify_log`` (§4.2.3 读写路由规则). Close paths cancel pending
notify in the same transaction (§7.2.11).
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
    CloseReason,
    GovernanceStatus,
    NotifyStatus,
)
from agentclaw.community.core.economy.governance.services.whitelist_service import (
    GovernanceWhitelistService,
)
from agentclaw.community.utils.env_utils import get_current_env


if TYPE_CHECKING:
    from agentclaw.community.core.economy.governance.domain.domain import (
        GovernanceNotification,
    )
    from agentclaw.community.core.economy.governance.domain.ticket import (
        GovernanceTicket,
    )
    from agentclaw.community.plugin_api.notify_sender import NotifySenderPlugin
    from agentclaw.community.core.economy.governance.repositories.audit_repo import (
        GovernanceAuditRepository,
    )
    from agentclaw.community.core.economy.governance.repositories.notify_log_repo import (
        NotifyLogRepository,
    )
    from agentclaw.community.core.economy.governance.repositories.task_record_repo import (
        TaskRecordRepository,
    )
    from agentclaw.community.plugin_api.cache_protocol import CachePlugin

log = get_logger(__name__)

_EMERGENCY_KEY_TEMPLATE = "governance:emergency:{env}"
_EMERGENCY_TTL_SECONDS = 7 * 24 * 3600  # 7 days


# ── Service I/O dataclasses (P5) ─────────────────────────────────────────


@dataclass(frozen=True)
class EmergencyState:
    """替代 get_state 返回的裸 dict。"""

    paused: bool
    reason: str | None
    operator: str | None
    paused_at: str | None
    pending_count: int
    open_count: int
    whitelist_count: int

    def to_dict(self) -> dict:
        return {
            "paused": self.paused,
            "reason": self.reason,
            "operator": self.operator,
            "paused_at": self.paused_at,
            "pending_count": self.pending_count,
            "open_count": self.open_count,
            "whitelist_count": self.whitelist_count,
        }


@dataclass(frozen=True)
class TicketActionOutcome:
    """pause_ticket / review_ticket / emergency_close 统一返回。"""

    ticket_id: str
    status: GovernanceStatus
    close_reason: str | None = None
    review_reason: str | None = None
    error: str | None = None
    error_code: str | None = None

    def to_dict(self) -> dict:
        return {
            "ticket_id": self.ticket_id,
            "governance_status": self.status.value if isinstance(self.status, GovernanceStatus) else self.status,
            "close_reason": self.close_reason,
            "review_reason": self.review_reason,
            "error": self.error,
            "error_code": self.error_code,
        }


@dataclass(frozen=True)
class BulkOperationResult:
    """cancel_pending / close_all_open / bulk_whitelist 返回。"""

    affected: int
    label: str  # "cancelled" / "closed" / "whitelisted"
    extra: dict | None = None

    def to_dict(self) -> dict:
        d: dict = {self.label: self.affected}
        if self.extra:
            d.update(self.extra)
        return d


class GovernanceAdminService:
    """Backend admin operations — emergency, bulk ops, review (§7.5)."""

    @inject
    def __init__(
        self,
        cache: CachePlugin,
        whitelist_service: GovernanceWhitelistService,
        notify_repo: NotifyLogRepository,
        audit_repo: GovernanceAuditRepository,
        task_repo: TaskRecordRepository,
        config: Any,  # EconomyGovernanceConfig
        notify_sender: NotifySenderPlugin,
    ) -> None:
        self._cache = cache
        self._whitelist_service = whitelist_service
        self._notify_repo = notify_repo
        self._audit_repo = audit_repo
        self._task_repo = task_repo
        self._config = config
        self._notify_sender = notify_sender
        self._emergency_key = _EMERGENCY_KEY_TEMPLATE.format(env=get_current_env())

    # -- State queries -------------------------------------------------------

    def is_paused(self) -> bool:
        """Check if the emergency brake is active."""
        try:
            raw = self._cache.get(self._emergency_key)
            if raw:
                data = json.loads(raw) if isinstance(raw, str) else raw
                return data.get("action") == "pause"
        except Exception:
            log.warning("[GovernanceEmergency] Failed to read emergency state")
        return False

    def get_state(self) -> EmergencyState:
        """Query current emergency state."""
        paused_info = self._read_pause_info()

        pending_count = self._notify_repo.count_pending()
        open_count = self._notify_repo.count_open_muted()
        whitelist_count = self._whitelist_service.count_by_type()

        return EmergencyState(
            paused=paused_info.get("action") == "pause",
            reason=paused_info.get("reason"),
            operator=paused_info.get("operator"),
            paused_at=paused_info.get("paused_at"),
            pending_count=pending_count,
            open_count=open_count,
            whitelist_count=whitelist_count,
        )

    # -- Actions ---------------------------------------------------------------

    def pause(self, reason: str, operator: str) -> None:
        """Pause scan + notification sending. Pending notifications preserved.

        Writes distributed cache key with 7-day TTL.
        """
        now = datetime.now()
        value = json.dumps({
            "action": "pause",
            "reason": reason,
            "operator": operator,
            "paused_at": now.isoformat(),
        })
        self._cache.set(self._emergency_key, value, ttl=_EMERGENCY_TTL_SECONDS)

        self._write_emergency_audit(
            action_taken=AuditAction.ADMIN_PAUSE,
            actor_id=operator,
            error_msg=f"reason={reason}; operator={operator}",
        )
        log.info(
            "[GovernanceEmergency] Paused by %s: %s", operator, reason,
        )

    def resume(self, reason: str, operator: str) -> None:
        """Resume normal operation. Deletes distributed cache key. Idempotent if not paused."""
        try:
            self._cache.delete(self._emergency_key)
        except Exception:
            log.warning("[GovernanceEmergency] Failed to delete emergency key, may already be gone")

        self._write_emergency_audit(
            action_taken=AuditAction.ADMIN_RESUME,
            actor_id=operator,
            error_msg=f"reason={reason}; operator={operator}",
        )
        log.info(
            "[GovernanceEmergency] Resumed by %s: %s", operator, reason,
        )

    def bulk_whitelist(
        self,
        bot_ids: list[str],
        reason: str,
        operator: str,
    ) -> dict:
        """Batch whitelist + cancel pending — delegates to WhitelistService."""
        return self._whitelist_service.bulk_whitelist(bot_ids, reason, operator)

    def cancel_pending(self, reason: str, operator: str) -> BulkOperationResult:
        """Cancel ALL pending notifications (emergency close).

        Returns ``BulkOperationResult(affected=N, label="cancelled")``.
        """
        now = datetime.now()
        cooldown_days = self._config.cooldown_days

        cancelled = self._notify_repo.bulk_close_open_muted(
            close_reason=CloseReason.EMERGENCY_CLOSED,
            closed_at=now,
            cooldown_until=now + timedelta(days=cooldown_days),
            only_unresponded=True,
        )

        self._write_emergency_audit(
            action_taken=AuditAction.ADMIN_CANCEL_PENDING,
            actor_id=operator,
            error_msg=f"reason={reason}; operator={operator}",
        )
        log.info(
            "[GovernanceEmergency] cancel_pending by %s: cancelled=%d",
            operator, cancelled,
        )
        return BulkOperationResult(affected=cancelled, label="cancelled")

    def close_all_open(self, reason: str, operator: str) -> BulkOperationResult:
        """Close ALL open/muted records, including already-responded ones.

        Unlike :meth:`cancel_pending` which only touches ``response IS NULL``
        records, this closes **every** open/muted notification regardless of
        whether the user has already responded (e.g. ``need_time`` → muted).

        Existing ``response`` / ``response_source`` / ``mute_until`` are
        preserved — only governance_status and close metadata are updated.

        Returns ``BulkOperationResult(affected=N, label="closed")``.
        """
        now = datetime.now()
        cooldown_days = self._config.cooldown_days

        closed = self._notify_repo.bulk_close_open_muted(
            close_reason=CloseReason.ADMIN_CLOSED,
            closed_at=now,
            cooldown_until=now + timedelta(days=cooldown_days),
            only_unresponded=False,
        )

        self._write_emergency_audit(
            action_taken=AuditAction.ADMIN_CLOSE_ALL,
            actor_id=operator,
            error_msg=f"reason={reason}; operator={operator}",
        )
        log.info(
            "[GovernanceAdmin] close_all_open by %s: closed=%d",
            operator, closed,
        )
        return BulkOperationResult(affected=closed, label="closed")

    # -- Ticket-level admin operations (§7.5) ----------------------------------

    def list_review_tickets(
        self,
        statuses: list[str] | None,
        *,
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[list[GovernanceTicket], int]:
        """评审工单列表:按治理状态过滤(跨 owner)、分页,返回领域模型 + 总数。

        Args:
            statuses: 治理状态白名单(open/scheduled/waiting_review/closed);
                None 时默认全部活跃态(open/scheduled/waiting_review);
                [] 显式表示无任何状态匹配 → 返回空(repo 层空列表短路)。
            offset: 分页偏移。
            limit: 分页上限。

        Returns:
            (工单领域模型列表, 满足条件的总数)。领域模型经 from_orm 灌入
            gmt_create/gmt_modified,评审列表直接用,router 层负责序列化。
        """
        effective = statuses if statuses is not None else [
            GovernanceStatus.OPEN.value,
            GovernanceStatus.SCHEDULED.value,
            GovernanceStatus.WAITING_REVIEW.value,
        ]
        tickets = self._task_repo.list_tickets_by_statuses(
            effective, offset=offset, limit=limit,
        )
        total = self._task_repo.count_tickets_by_statuses(effective)
        return tickets, total

    def get_review_ticket_detail(
        self, ticket_id: str,
    ) -> GovernanceTicket | None:
        """评审工单详情:取单个工单领域模型,供详情面板展示。

        Args:
            ticket_id: 工单稳定 UUID。

        Returns:
            :class:`GovernanceTicket` 或 None(不存在)。
        """
        return self._task_repo.find_by_ticket_id(ticket_id)

    def pause_ticket(
        self, ticket_id: str, admin_id: str, reason: str = "",
    ) -> TicketActionOutcome:
        """Admin pause: open/scheduled → waiting_review (§7.5.1)."""
        ticket = self._task_repo.find_by_ticket_id(ticket_id)
        if not ticket:
            return TicketActionOutcome(
                ticket_id=ticket_id, status=GovernanceStatus.OPEN,
                error="Ticket not found", error_code="NOT_FOUND",
            )

        if ticket.governance_status not in (GovernanceStatus.OPEN, GovernanceStatus.SCHEDULED):
            return TicketActionOutcome(
                ticket_id=ticket_id,
                status=GovernanceStatus(ticket.governance_status),
                error=f"Cannot pause ticket in status={ticket.governance_status}",
                error_code="INVALID_STATUS",
            )

        # Update via Repo command method
        self._task_repo.pause_ticket(ticket_id, review_reason="admin_paused")

        if ticket.ticket_id:
            self._notify_repo.cancel_pending_by_ticket(ticket.ticket_id)

        self._audit_repo.add_audit(
            "admin-pause",
            bot_id=ticket.bot_id,
            owner_id=ticket.owner_id,
            actor_id=admin_id,
            action_taken=AuditAction.PAUSED_FOR_REVIEW,
            source="admin_api",
            error_msg=f"ticket_id={ticket_id}; reason={reason}",
            dry_run=0,
        )

        return TicketActionOutcome(
            ticket_id=ticket_id,
            status=GovernanceStatus.WAITING_REVIEW,
            review_reason="admin_paused",
        )

    def review_ticket(
        self, ticket_id: str, action: str, admin_id: str, remark: str = "",
    ) -> TicketActionOutcome:
        """Admin review: waiting_review → closed (§7.5.2).

        Actions: approve_close / approve_whitelist / reject_for_reopen.
        """
        valid_actions = {"approve_close", "approve_whitelist", "reject_for_reopen"}
        if action not in valid_actions:
            return TicketActionOutcome(
                ticket_id=ticket_id, status=GovernanceStatus.WAITING_REVIEW,
                error=f"Invalid action: {action}", error_code="INVALID_ACTION",
            )

        ticket = self._task_repo.find_by_ticket_id(ticket_id)
        if not ticket:
            return TicketActionOutcome(
                ticket_id=ticket_id, status=GovernanceStatus.WAITING_REVIEW,
                error="Ticket not found", error_code="NOT_FOUND",
            )

        if ticket.governance_status != GovernanceStatus.WAITING_REVIEW:
            return TicketActionOutcome(
                ticket_id=ticket_id,
                status=GovernanceStatus(ticket.governance_status),
                error=f"Ticket not in waiting_review (status={ticket.governance_status})",
                error_code="INVALID_STATUS",
            )

        now = datetime.now()
        cooldown_days = self._config.cooldown_days

        close_reason: str
        cooldown_until: datetime | None = None

        if action == "approve_close":
            review_reason = ticket.review_reason or "unknown"
            close_reason = f"{review_reason}_approved"
            cooldown_until = now + timedelta(days=cooldown_days)
        elif action == "approve_whitelist":
            close_reason = "whitelist_approved"
            cooldown_until = None
            try:
                self._whitelist_service.add(
                    bot_id=ticket.bot_id,
                    owner_id=ticket.owner_id,
                    created_by=admin_id,
                    whitelist_type="governance",
                    source="admin_review",
                )
            except Exception:
                log.exception(
                    "[GovernanceAdmin] Failed to add whitelist for bot_id=%s",
                    ticket.bot_id,
                )
        elif action == "reject_for_reopen":
            close_reason = "review_rejected"
            cooldown_until = None

        # Update via Repo command method
        self._task_repo.review_ticket(
            ticket_id,
            review_decision=action,
            reviewed_by=admin_id,
            reviewed_at=now,
            close_reason=close_reason,
            cooldown_until=cooldown_until,
            review_remark=remark,
        )

        if ticket.ticket_id:
            self._notify_repo.cancel_pending_by_ticket(ticket.ticket_id)

        audit_action_map = {
            "approve_close": AuditAction.REVIEW_APPROVE_CLOSE,
            "approve_whitelist": AuditAction.REVIEW_APPROVE_WHITELIST,
            "reject_for_reopen": AuditAction.REVIEW_REJECT_FOR_REOPEN,
        }
        self._audit_repo.add_audit(
            "admin-review",
            bot_id=ticket.bot_id,
            owner_id=ticket.owner_id,
            actor_id=admin_id,
            action_taken=audit_action_map.get(action, action),
            source="admin_api",
            error_msg=f"ticket_id={ticket_id}; action={action}; remark={remark}",
            dry_run=0,
        )

        return TicketActionOutcome(
            ticket_id=ticket_id,
            status=GovernanceStatus.CLOSED,
            close_reason=close_reason,
        )

    def emergency_close(
        self, ticket_id: str, admin_id: str, reason: str = "",
    ) -> TicketActionOutcome:
        """Immediate ticket close without cooldown (§6.3)."""
        ticket = self._task_repo.find_by_ticket_id(ticket_id)
        if not ticket:
            return TicketActionOutcome(
                ticket_id=ticket_id, status=GovernanceStatus.OPEN,
                error="Ticket not found", error_code="NOT_FOUND",
            )

        if ticket.governance_status == GovernanceStatus.CLOSED:
            return TicketActionOutcome(
                ticket_id=ticket_id,
                status=GovernanceStatus.CLOSED,
                close_reason=ticket.close_reason,
            )

        now = datetime.now()

        # Update via Repo command method
        self._task_repo.close_ticket(
            ticket_id,
            close_reason=CloseReason.EMERGENCY_CLOSED,
            closed_at=now,
        )

        if ticket.ticket_id:
            self._notify_repo.cancel_pending_by_ticket(ticket.ticket_id)

        self._audit_repo.add_audit(
            "admin-emergency-close",
            bot_id=ticket.bot_id,
            owner_id=ticket.owner_id,
            actor_id=admin_id,
            action_taken=AuditAction.ADMIN_CLOSE_ALL,
            source="admin_api",
            error_msg=f"ticket_id={ticket_id}; reason={reason}",
            dry_run=0,
        )

        return TicketActionOutcome(
            ticket_id=ticket_id,
            status=GovernanceStatus.CLOSED,
            close_reason=CloseReason.EMERGENCY_CLOSED,
        )

    # -- Records delete (emergency) — moved from router ------------------------

    def delete_records(self, body: dict, operator: str) -> dict:
        """Emergency delete for record_daily or notify_log.

        ``body`` keys: table, dry_run, reason, dt_versions, ids, notification_ids.
        """
        run_id = f"delete-{uuid.uuid4().hex[:8]}"

        if body["table"] == "record_daily":
            return self._delete_record_daily(body, operator, run_id)
        else:
            return self._delete_notify_log(body, operator, run_id)

    def _delete_record_daily(
        self, body: dict, operator: str, run_id: str,
    ) -> dict:
        would_delete = 0
        not_found: list[int | str] = []

        if body.get("dt_versions"):
            per_ver = self._task_repo.count_by_dt_versions(
                body["dt_versions"],
            )
            would_delete += sum(per_ver.values())

        if body.get("ids"):
            match_count, nf_ids = self._task_repo.count_by_ids(
                body["ids"],
            )
            would_delete += match_count
            not_found.extend(nf_ids)

        if not body["dry_run"]:
            deleted = 0
            if body.get("dt_versions"):
                deleted += self._task_repo.delete_by_dt_versions(
                    body["dt_versions"],
                )
            if body.get("ids"):
                del_count, _ = self._task_repo.delete_by_ids(
                    body["ids"],
                )
                deleted += del_count

            self._audit_repo.add_audit(
                run_id, actor_id=operator,
                action_taken=AuditAction.RECORDS_DELETED,
                source="admin_api",
                error_msg=(
                    f"reason={body.get('reason', '')} "
                    f"table=record_daily "
                    f"dt_versions={body.get('dt_versions')} "
                    f"ids={body.get('ids')} "
                    f"deleted={deleted}"
                ),
                dry_run=0,
            )
        else:
            deleted = 0

        return {
            "table": body["table"],
            "dry_run": body["dry_run"],
            "would_delete": would_delete,
            "deleted": deleted,
            "not_found": not_found,
        }

    def _delete_notify_log(
        self, body: dict, operator: str, run_id: str,
    ) -> dict:
        would_delete = 0
        not_found: list[int | str] = []

        if body.get("notification_ids"):
            match_count, nf_ids = self._notify_repo.count_by_notification_ids(
                body["notification_ids"],
            )
            would_delete = match_count
            not_found.extend(nf_ids)

        if not body["dry_run"]:
            deleted = 0
            if body.get("notification_ids"):
                del_count, _ = self._notify_repo.delete_by_notification_ids(
                    body["notification_ids"],
                )
                deleted = del_count

            self._audit_repo.add_audit(
                run_id, actor_id=operator,
                action_taken=AuditAction.NOTIFICATIONS_DELETED,
                source="admin_api",
                error_msg=(
                    f"reason={body.get('reason', '')} "
                    f"table=notify_log "
                    f"notification_ids={body.get('notification_ids')} "
                    f"deleted={deleted}"
                ),
                dry_run=0,
            )
        else:
            deleted = 0

        return {
            "table": body["table"],
            "dry_run": body["dry_run"],
            "would_delete": would_delete,
            "deleted": deleted,
            "not_found": not_found,
        }

# ------------------------------------------------------------------
    # Whitelist delete — delegates to GovernanceWhitelistService
    # ------------------------------------------------------------------

    def delete_whitelist_entry(
        self,
        *,
        bot_id: str,
        owner_id: str,
        reason: str,
        operator: str,
    ) -> dict:
        """Delete a single whitelist entry — delegates to WhitelistService."""
        return self._whitelist_service.delete_whitelist_entry(
            bot_id=bot_id, owner_id=owner_id, reason=reason, operator=operator,
        )

    # -- Deliver pending — moved from router (scan_and_deliver Phase 2-5) ------

    def deliver_pending(
        self,
        *,
        scan_svc: Any,
        override_recipient: str,
        dry_run: bool,
        max_send: int,
        channel: str,
        skip_scan: bool,
        scan_dry_run: bool,
    ) -> dict:
        """Orchestrate: read pending → build → send → update DB + audit.

        Phase 1 (cron tick) is the caller's responsibility — this method
        handles Phase 2-5 only.
        """
        from agentclaw.community.core.economy.governance.services.notify_builder_service import (
            build_card_notification_data,
            build_governance_reason,
            build_tc_card_detail_link,
        )

        # ---- Phase 2: Read pending notifications ----
        pending_rows = self._notify_repo.list_pending_for_cron()

        if not pending_rows:
            return {
                "total": 0,
                "dry_run": dry_run,
                "override_recipient": override_recipient,
                "channel": channel,
                "sent_count": 0,
                "results": [],
            }

        if max_send and max_send > 0:
            pending_rows = pending_rows[:max_send]

        # ---- Phase 3: Build delivery list ----
        deliveries: list[dict] = []
        for p in pending_rows:
            effective_channel = p.channel if channel == "auto" else channel
            deliveries.append({
                "notification_id": p.notification_id,
                "ticket_id": p.ticket_id,
                "bot_id": p.bot_id,
                "bot_name": p.bot_name or "N/A",
                "owner_id": p.owner_id,
                "dt_version": p.dt_version,
                "hit_dimensions": p.triggered_dimensions,
                "governance_max_priority": p.severity,
                "expected_token_saving": p.estimated_saving_tokens,
                "saving_ratio": p.saving_ratio,
                "notification_md": p.notification_md or "",
                "notification_structured": p.notification_structured,
                "notify_channel": effective_channel,
                "notify_type": p.notify_type,
                "original_recipient": p.owner_id,
                "override_recipient": override_recipient,
                "title": "🔔 Bot 治理通知",
                "content": p.notification_md or "",
                "send_attempt_count": p.send_attempt_count,
            })

        # ---- Phase 4: Build payloads & optionally send ----
        results: list[dict] = []
        sent_count = 0
        for d in deliveries:
            notify_channel = d["notify_channel"]
            msg_id: str | None = None
            channel_used = notify_channel

            if notify_channel == "tc_card":
                detail_link = ""
                tc_card_extra: dict[str, Any] = {}
                try:
                    notification_data = build_card_notification_data(
                        notification_structured=d["notification_structured"],
                        notification_id=d["notification_id"],
                        bot_id=d["bot_id"],
                        bot_name=d["bot_name"],
                        owner_id=d["original_recipient"],
                        governance_max_priority=d.get("governance_max_priority"),
                        expected_token_saving=d.get("expected_token_saving"),
                        saving_ratio=d.get("saving_ratio"),
                    )
                    reason = build_governance_reason(
                        notification_structured=d["notification_structured"],
                        bot_name=d["bot_name"],
                        dt_version=d.get("dt_version"),
                        hit_dimensions=d.get("hit_dimensions"),
                        governance_max_priority=d.get("governance_max_priority"),
                        expected_token_saving=d.get("expected_token_saving"),
                        saving_ratio=d.get("saving_ratio"),
                    )
                    detail_link = build_tc_card_detail_link(
                        bot_id=d["bot_id"],
                        card_id=self._config.tc_card_id,
                        notification_data=notification_data,
                        base_url=self._config.tc_card_preview_url,
                        iframe_callback_url=self._config.iframe_callback_url,
                        staff_id=override_recipient,
                    )
                    tc_card_extra = {
                        "bot_id": d["bot_id"],
                        "card_id": self._config.tc_card_id,
                        "notification_data": notification_data,
                        "out_track_id_prefix": "gov-notify",
                    }
                except Exception:
                    log.exception(
                        "[DeliverPending] TC card build failed for %s, degrading to Markdown",
                        d["notification_id"],
                    )
                    notification_data = None
                    reason = None
                    detail_link = ""
                    tc_card_extra = {}
                    channel_used = "markdown"

                if not dry_run and tc_card_extra:
                    from agentclaw.community.plugin_api.notify_sender import NotifyMessage
                    msg = NotifyMessage(
                        title=d["title"],
                        body=reason or "",
                        recipient=override_recipient,
                        deep_link=detail_link,
                        extra=tc_card_extra,
                    )
                    msg_id = self._notify_sender.send(msg, channel="tc_card")

                if not dry_run and msg_id is None and notify_channel == "tc_card":
                    log.warning(
                        "[DeliverPending] TC card failed for %s, degrading to Markdown",
                        d["notification_id"],
                    )
                    channel_used = "markdown"

                if not dry_run and channel_used == "markdown":
                    from agentclaw.community.plugin_api.notify_sender import NotifyMessage as _NM
                    msg_md = _NM(
                        title=d["title"],
                        body=d["content"],
                        recipient=override_recipient,
                    )
                    msg_id = self._notify_sender.send(msg_md, channel="markdown")

                if dry_run:
                    tc_preview = {
                        "reason_preview": (reason or "")[:200],
                        "detail_link": detail_link,
                        "notification_data_keys": (
                            list(notification_data.keys()) if notification_data else []
                        ),
                    }
                    results.append({
                        "notification_id": d["notification_id"],
                        "bot_name": d["bot_name"],
                        "original_recipient": d["original_recipient"],
                        "sent_to": override_recipient,
                        "channel": channel_used,
                        "dry_run": True,
                        "tc_card": tc_preview,
                    })
                    continue
            else:
                if not dry_run:
                    from agentclaw.community.plugin_api.notify_sender import NotifyMessage as _NM
                    msg_plain = _NM(
                        title=d["title"],
                        body=d["content"],
                        recipient=override_recipient,
                    )
                    msg_id = self._notify_sender.send(msg_plain, channel="markdown")

                if dry_run:
                    results.append({
                        "notification_id": d["notification_id"],
                        "bot_name": d["bot_name"],
                        "original_recipient": d["original_recipient"],
                        "sent_to": override_recipient,
                        "channel": channel_used,
                        "dry_run": True,
                    })
                    continue

            ok = msg_id is not None
            if ok:
                sent_count += 1
            results.append({
                "notification_id": d["notification_id"],
                "bot_name": d["bot_name"],
                "original_recipient": d["original_recipient"],
                "sent_to": override_recipient,
                "channel": channel_used,
                "dry_run": False,
                "success": ok,
                "external_message_id": msg_id,
            })

        # ---- Phase 5: Update notify_status + audit (live only) ----
        if not dry_run:
            audit_run_id = f"deliver-{uuid.uuid4().hex[:8]}"
            now = datetime.now()

            # Build lookup from Phase 2 domain objects (avoids re-querying)
            pending_by_id: dict[str, GovernanceNotification] = {
                p.notification_id: p for p in pending_rows
            }

            # ---- Sent: update delivery status + audit ----
            for r in results:
                if not r.get("success"):
                    continue
                nid = r["notification_id"]
                p = pending_by_id.get(nid)
                if p is None:
                    continue
                result_channel = r.get("channel")
                original_channel = p.channel or "markdown"
                self._notify_repo.update_delivery_status(
                    nid,
                    status=NotifyStatus.SENT,
                    external_id=r.get("external_message_id"),
                    at=now,
                    channel=result_channel if result_channel and result_channel != original_channel else None,
                )
                self._audit_repo.add_audit(
                    audit_run_id, p.bot_id, p.owner_id,
                    notification_id=nid,
                    check_result=p.decision_at_create,
                    governance_decision=p.decision_at_create,
                    hit_dimensions=p.triggered_dimensions,
                    expected_token_saving=p.estimated_saving_tokens,
                    saving_ratio=p.saving_ratio,
                    action_taken=AuditAction.NOTIFICATION_SENT,
                    source="scan_and_deliver",
                    dry_run=0,
                )

            # ---- Failed: audit only ----
            for r in results:
                if r.get("success"):
                    continue
                nid = r["notification_id"]
                p = pending_by_id.get(nid)
                if p is None:
                    continue
                self._audit_repo.add_audit(
                    audit_run_id, p.bot_id, p.owner_id,
                    notification_id=nid,
                    check_result=p.decision_at_create,
                    governance_decision=p.decision_at_create,
                    hit_dimensions=p.triggered_dimensions,
                    expected_token_saving=p.estimated_saving_tokens,
                    saving_ratio=p.saving_ratio,
                    action_taken=AuditAction.NOTIFICATION_SEND_FAILED,
                    source="scan_and_deliver",
                    dry_run=0,
                )

        return {
            "total": len(deliveries),
            "dry_run": dry_run,
            "override_recipient": override_recipient,
            "channel": channel,
            "sent_count": sent_count,
            "results": results,
        }

    # -- Internal --------------------------------------------------------------

    def _read_pause_info(self) -> dict:
        """Read the pause info from distributed cache."""
        try:
            raw = self._cache.get(self._emergency_key)
            if raw:
                return json.loads(raw) if isinstance(raw, str) else raw
        except Exception:
            pass
        return {}

    def _write_emergency_audit(
        self,
        *,
        action_taken: str,
        actor_id: str | None = None,
        error_msg: str = "",
    ) -> None:
        """Best-effort audit write for emergency operations.

        Delegates to :meth:`GovernanceAuditRepository.add_audit` with the
        emergency-specific ``run_id`` and ``source``.
        """
        try:
            self._audit_repo.add_audit(
                "emergency",
                action_taken=action_taken,
                actor_id=actor_id,
                error_msg=error_msg,
                source="admin_api",
                dry_run=0,
            )
        except Exception:
            log.exception("[GovernanceEmergency] Failed to write audit for %s", action_taken)
