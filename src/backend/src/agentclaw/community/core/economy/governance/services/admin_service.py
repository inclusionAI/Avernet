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
  - delete_whitelist_entries — delegate to :class:`GovernanceWhitelistService`
  - deliver_pending — scan-and-deliver pipeline (testing tool)

All ticket lifecycle transitions land on ``task_record`` — never on
``notify_log`` (§4.2.3 读写路由规则). Close paths cancel pending
notify in the same transaction (§7.2.11).
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from injector import inject

from agentclaw.community.core.economy.governance.contracts.enums import AuditAction
from agentclaw.community.core.economy.governance.contracts.models import (
    GovernanceNotifyLog,
    GovernanceTaskRecordDaily,
)
from agentclaw.community.core.economy.governance.services.whitelist_service import (
    GovernanceWhitelistService,
)
from agentclaw.community.utils.env_utils import get_current_env


if TYPE_CHECKING:
    from agentclaw.community.core.economy.governance.contracts.protocols import (
        GovernanceNotifySender,
    )
    from agentclaw.community.core.economy.governance.repositories.audit_repo import (
        GovernanceAuditRepository,
    )
    from agentclaw.community.core.economy.governance.repositories.notify_log_repo import (
        NotifyLogRepository,
    )
    from agentclaw.community.core.economy.governance.repositories.task_record_repo import (
        TaskRecordRepository,
    )
    from agentclaw.community.di.config import GovernanceDingTalkConfig
    from agentclaw.community.plugin_api.cache_protocol import CachePlugin
    from agentclaw.community.plugin_api.database_protocol import DatabasePlugin

log = logging.getLogger(__name__)

_EMERGENCY_KEY_TEMPLATE = "governance:emergency:{env}"
_EMERGENCY_TTL_SECONDS = 7 * 24 * 3600  # 7 days


class GovernanceAdminService:
    """Backend admin operations — emergency, bulk ops, review (§7.5)."""

    @inject
    def __init__(
        self,
        db: DatabasePlugin,
        cache: CachePlugin,
        whitelist_service: GovernanceWhitelistService,
        notify_repo: NotifyLogRepository,
        audit_repo: GovernanceAuditRepository,
        task_repo: TaskRecordRepository,
        config: Any,  # EconomyGovernanceConfig
        notify_sender: GovernanceNotifySender,
        dingtalk_config: GovernanceDingTalkConfig,
    ) -> None:
        self._db = db
        self._cache = cache
        self._whitelist_service = whitelist_service
        self._notify_repo = notify_repo
        self._audit_repo = audit_repo
        self._task_repo = task_repo
        self._config = config
        self._notify_sender = notify_sender
        self._dingtalk_config = dingtalk_config
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

    def get_state(self) -> dict:
        """Query current emergency state.

        Returns:
            ``{"paused": bool, "reason": str|None, "operator": str|None,
             "paused_at": str|None, "pending_count": int, "open_count": int,
             "whitelist_count": int}``
        """
        paused_info = self._read_pause_info()

        pending_count = self._notify_repo.count_pending()
        open_count = self._notify_repo.count_open_muted()
        whitelist_count = self._whitelist_service.count_by_type()

        return {
            "paused": paused_info.get("action") == "pause",
            "reason": paused_info.get("reason"),
            "operator": paused_info.get("operator"),
            "paused_at": paused_info.get("paused_at"),
            "pending_count": pending_count,
            "open_count": open_count,
            "whitelist_count": whitelist_count,
        }

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

    def cancel_pending(self, reason: str, operator: str) -> dict:
        """Cancel ALL pending notifications (emergency close).

        Returns ``{"cancelled": N}``.
        """
        now = datetime.now()
        cooldown_days = self._config.cooldown_days

        # Repo returns detached rows (self-managed session). Re-query in our own session.
        with self._db.orm_session() as s:
            affected = (
                s.query(GovernanceNotifyLog)
                .filter(
                    GovernanceNotifyLog.response.is_(None),
                    GovernanceNotifyLog.governance_status.in_(["open", "muted"]),
                )
                .all()
            )
            cancelled = 0
            for row in affected:
                row.notify_status = "cancelled"
                row.governance_status = "closed"
                row.close_reason = "emergency_closed"
                row.closed_at = now
                row.cooldown_until = now + timedelta(days=cooldown_days)
                cancelled += 1

        self._write_emergency_audit(
            action_taken=AuditAction.ADMIN_CANCEL_PENDING,
            actor_id=operator,
            error_msg=f"reason={reason}; operator={operator}",
        )
        log.info(
            "[GovernanceEmergency] cancel_pending by %s: cancelled=%d",
            operator, cancelled,
        )
        return {"cancelled": cancelled}

    def close_all_open(self, reason: str, operator: str) -> dict:
        """Close ALL open/muted records, including already-responded ones.

        Unlike :meth:`cancel_pending` which only touches ``response IS NULL``
        records, this closes **every** open/muted notification regardless of
        whether the user has already responded (e.g. ``need_time`` → muted).

        Existing ``response`` / ``response_source`` / ``mute_until`` are
        preserved — only governance_status and close metadata are updated.

        Returns ``{"closed": N}``.
        """
        now = datetime.now()
        cooldown_days = self._config.cooldown_days

        # Repo returns detached rows (self-managed session). Re-query in our own session.
        with self._db.orm_session() as s:
            affected = (
                s.query(GovernanceNotifyLog)
                .filter(
                    GovernanceNotifyLog.governance_status.in_(["open", "muted"]),
                )
                .all()
            )
            closed = 0
            for row in affected:
                # Cancel pending sends; preserve already-sent status
                if row.notify_status == "pending":
                    row.notify_status = "cancelled"
                row.governance_status = "closed"
                row.close_reason = "admin_closed"
                row.closed_at = now
                row.cooldown_until = now + timedelta(days=cooldown_days)
                closed += 1

        self._write_emergency_audit(
            action_taken=AuditAction.ADMIN_CLOSE_ALL,
            actor_id=operator,
            error_msg=f"reason={reason}; operator={operator}",
        )
        log.info(
            "[GovernanceAdmin] close_all_open by %s: closed=%d",
            operator, closed,
        )
        return {"closed": closed}

    # -- Ticket-level admin operations (§7.5) ----------------------------------

    def pause_ticket(
        self, ticket_id: str, admin_id: str, reason: str = "",
    ) -> dict:
        """Admin pause: open/scheduled → waiting_review (§7.5.1)."""
        ticket = self._task_repo.find_by_ticket_id(ticket_id)
        if not ticket:
            return {"error": "Ticket not found", "error_code": "NOT_FOUND"}

        if ticket.get("governance_status") not in ("open", "scheduled"):
            return {
                "error": f"Cannot pause ticket in status={ticket.get('governance_status')}",
                "error_code": "INVALID_STATUS",
            }

        # Update via self-managed session
        with self._db.orm_session() as s:
            db_ticket = (
                s.query(GovernanceTaskRecordDaily)
                .filter(GovernanceTaskRecordDaily.ticket_id == ticket_id)
                .one_or_none()
            )
            if db_ticket is None:
                return {"error": "Ticket not found", "error_code": "NOT_FOUND"}
            db_ticket.governance_status = "waiting_review"
            db_ticket.review_reason = "admin_paused"
            db_ticket.remind_at = None

        if ticket.get("ticket_id"):
            self._notify_repo.cancel_pending_by_ticket(ticket.get("ticket_id"))

        self._audit_repo.add_audit(
            "admin-pause",
            bot_id=ticket.get("bot_id"),
            owner_id=ticket.get("owner_id"),
            actor_id=admin_id,
            action_taken=AuditAction.PAUSED_FOR_REVIEW,
            source="admin_api",
            error_msg=f"ticket_id={ticket_id}; reason={reason}",
            dry_run=0,
        )

        return {
            "ticket_id": ticket_id,
            "governance_status": "waiting_review",
            "review_reason": "admin_paused",
        }

    def review_ticket(
        self, ticket_id: str, action: str, admin_id: str, remark: str = "",
    ) -> dict:
        """Admin review: waiting_review → closed (§7.5.2).

        Actions: approve_close / approve_whitelist / reject_for_reopen.
        """
        valid_actions = {"approve_close", "approve_whitelist", "reject_for_reopen"}
        if action not in valid_actions:
            return {"error": f"Invalid action: {action}", "error_code": "INVALID_ACTION"}

        ticket = self._task_repo.find_by_ticket_id(ticket_id)
        if not ticket:
            return {"error": "Ticket not found", "error_code": "NOT_FOUND"}

        if ticket.get("governance_status") != "waiting_review":
            return {
                "error": f"Ticket not in waiting_review (status={ticket.get('governance_status')})",
                "error_code": "INVALID_STATUS",
            }

        now = datetime.now()
        cooldown_days = self._config.cooldown_days

        close_reason: str
        cooldown_until: datetime | None = None

        if action == "approve_close":
            review_reason = ticket.get("review_reason") or "unknown"
            close_reason = f"{review_reason}_approved"
            cooldown_until = now + timedelta(days=cooldown_days)
        elif action == "approve_whitelist":
            close_reason = "whitelist_approved"
            cooldown_until = None
            try:
                self._whitelist_service.batch_add(
                    entries=[{"bot_id": ticket.get("bot_id"), "owner_id": ticket.get("owner_id")}],
                    created_by=admin_id,
                    whitelist_type="governance",
                    source="admin_review",
                )
            except Exception:
                log.exception(
                    "[GovernanceAdmin] Failed to add whitelist for bot_id=%s",
                    ticket.get("bot_id"),
                )
        elif action == "reject_for_reopen":
            close_reason = "review_rejected"
            cooldown_until = None

        # Update via self-managed session
        with self._db.orm_session() as s:
            db_ticket = (
                s.query(GovernanceTaskRecordDaily)
                .filter(GovernanceTaskRecordDaily.ticket_id == ticket_id)
                .one_or_none()
            )
            if db_ticket is None:
                return {"error": "Ticket not found", "error_code": "NOT_FOUND"}
            db_ticket.governance_status = "closed"
            db_ticket.active_worker = None
            db_ticket.closed_at = now
            db_ticket.review_decision = action
            db_ticket.reviewed_by = admin_id
            db_ticket.reviewed_at = now
            db_ticket.review_remark = remark
            db_ticket.remind_at = None
            db_ticket.close_reason = close_reason
            db_ticket.cooldown_until = cooldown_until

        if ticket.get("ticket_id"):
            self._notify_repo.cancel_pending_by_ticket(ticket.get("ticket_id"))

        audit_action_map = {
            "approve_close": AuditAction.REVIEW_APPROVE_CLOSE,
            "approve_whitelist": AuditAction.REVIEW_APPROVE_WHITELIST,
            "reject_for_reopen": AuditAction.REVIEW_REJECT_FOR_REOPEN,
        }
        self._audit_repo.add_audit(
            "admin-review",
            bot_id=ticket.get("bot_id"),
            owner_id=ticket.get("owner_id"),
            actor_id=admin_id,
            action_taken=audit_action_map.get(action, action),
            source="admin_api",
            error_msg=f"ticket_id={ticket_id}; action={action}; remark={remark}",
            dry_run=0,
        )

        return {
            "ticket_id": ticket_id,
            "governance_status": "closed",
            "close_reason": close_reason,
        }

    def emergency_close(
        self, ticket_id: str, admin_id: str, reason: str = "",
    ) -> dict:
        """Immediate ticket close without cooldown (§6.3)."""
        ticket = self._task_repo.find_by_ticket_id(ticket_id)
        if not ticket:
            return {"error": "Ticket not found", "error_code": "NOT_FOUND"}

        if ticket.get("governance_status") == "closed":
            return {
                "ticket_id": ticket_id,
                "governance_status": "closed",
                "close_reason": ticket.get("close_reason"),
            }

        now = datetime.now()

        # Update via self-managed session
        with self._db.orm_session() as s:
            db_ticket = (
                s.query(GovernanceTaskRecordDaily)
                .filter(GovernanceTaskRecordDaily.ticket_id == ticket_id)
                .one_or_none()
            )
            if db_ticket is None:
                return {"error": "Ticket not found", "error_code": "NOT_FOUND"}
            db_ticket.governance_status = "closed"
            db_ticket.close_reason = "emergency_closed"
            db_ticket.active_worker = None
            db_ticket.closed_at = now
            db_ticket.cooldown_until = None
            db_ticket.remind_at = None

        if ticket.get("ticket_id"):
            self._notify_repo.cancel_pending_by_ticket(ticket.get("ticket_id"))

        self._audit_repo.add_audit(
            "admin-emergency-close",
            bot_id=ticket.get("bot_id"),
            owner_id=ticket.get("owner_id"),
            actor_id=admin_id,
            action_taken=AuditAction.ADMIN_CLOSE_ALL,
            source="admin_api",
            error_msg=f"ticket_id={ticket_id}; reason={reason}",
            dry_run=0,
        )

        return {
            "ticket_id": ticket_id,
            "governance_status": "closed",
            "close_reason": "emergency_closed",
        }

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

    def delete_whitelist_entries(self, body: dict, operator: str) -> dict:
        """Delete whitelist entries — delegates to WhitelistService."""
        return self._whitelist_service.delete_whitelist_entries(body, operator)

    # -- Deliver pending — moved from router (scan_and_deliver Phase 2-5) ------

    async def deliver_pending(
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
        from agentclaw.community.core.economy.governance.contracts.models import GovernanceNotifyLog
        from agentclaw.community.core.economy.governance.services.notify_builder_service import (
            build_card_notification_data,
            build_governance_reason,
            build_tc_card_detail_link,
        )

        # ---- Phase 2: Read pending notifications ----
        pending_rows: list[dict] = []
        with self._db.orm_session() as session:
            rows = (
                session.query(GovernanceNotifyLog)
                .filter(GovernanceNotifyLog.notify_status == "pending")
                .all()
            )
            pending_rows = [r.to_delivery_dict() for r in rows]

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
            effective_channel = p["notify_channel"] if channel == "auto" else channel
            deliveries.append({
                **p,
                "original_recipient": p["owner_id"],
                "override_recipient": override_recipient,
                "title": "🔔 Bot 治理通知",
                "content": p["notification_md"],
                "notify_channel": effective_channel,
            })

        # ---- Phase 4: Build payloads & optionally send ----
        results: list[dict] = []
        sent_count = 0
        for d in deliveries:
            notify_channel = d["notify_channel"]
            msg_id: str | None = None
            channel_used = notify_channel

            if notify_channel == "tc_card":
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
                        iframe_callback_url=self._dingtalk_config.iframe_callback_url,
                        staff_id=override_recipient,
                    )
                except Exception:
                    log.exception(
                        "[deliver-pending] TC card build failed for %s, degrading to Markdown",
                        d["notification_id"],
                    )
                    notification_data = None
                    reason = None
                    detail_link = None

                if not dry_run and notification_data is not None:
                    msg_id = self._notify_sender.send_tc_card(
                        user_id=override_recipient,
                        reason=reason or "",
                        detail_link=detail_link or "",
                        bot_id=d["bot_id"],
                        card_id=self._config.tc_card_id,
                        notification_data=notification_data,
                        out_track_id_prefix="gov-notify",
                    )

                if not dry_run and msg_id is None and notify_channel == "tc_card":
                    log.warning(
                        "[deliver-pending] TC card failed for %s, degrading to Markdown",
                        d["notification_id"],
                    )
                    channel_used = "markdown"

                if not dry_run and channel_used == "markdown":
                    msg_id = self._notify_sender.send_markdown(
                        user_id=override_recipient,
                        title=d["title"],
                        content=d["content"],
                    )

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
                    msg_id = self._notify_sender.send_markdown(
                        user_id=override_recipient,
                        title=d["title"],
                        content=d["content"],
                    )

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
            with self._db.orm_session() as session:
                audit_run_id = f"deliver-{uuid.uuid4().hex[:8]}"
                sent_ids = {r["notification_id"] for r in results if r.get("success")}
                failed_ids = {r["notification_id"] for r in results if not r.get("success")}
                updated = (
                    session.query(GovernanceNotifyLog)
                    .filter(GovernanceNotifyLog.notification_id.in_(sent_ids))
                    .all()
                ) if sent_ids else []
                now = datetime.now()
                for row in updated:
                    row.notify_status = "sent"
                    row.sent_at = now
                    matched = [
                        r for r in results
                        if r["notification_id"] == row.notification_id
                    ]
                    row.external_message_id = (
                        matched[0].get("external_message_id") if matched else None
                    )
                    result_channel = matched[0].get("channel") if matched else None
                    if result_channel and result_channel != getattr(
                        row, "notify_channel", "markdown",
                    ):
                        row.notify_channel = result_channel
                    self._audit_repo.add_audit(
                        audit_run_id, row.bot_id, row.owner_id,
                        notification_id=row.notification_id,
                        check_result=row.governance_decision,
                        governance_decision=row.governance_decision,
                        hit_dimensions=row.hit_dimensions,
                        expected_token_saving=row.expected_token_saving,
                        saving_ratio=float(row.saving_ratio)
                        if row.saving_ratio else None,
                        action_taken=AuditAction.NOTIFICATION_SENT,
                        source="scan_and_deliver",
                        dry_run=0,
                    )

                if failed_ids:
                    failed_rows = (
                        session.query(GovernanceNotifyLog)
                        .filter(GovernanceNotifyLog.notification_id.in_(failed_ids))
                        .all()
                    )
                    for row in failed_rows:
                        self._audit_repo.add_audit(
                            audit_run_id, row.bot_id, row.owner_id,
                            notification_id=row.notification_id,
                            check_result=row.governance_decision,
                            governance_decision=row.governance_decision,
                            hit_dimensions=row.hit_dimensions,
                            expected_token_saving=row.expected_token_saving,
                            saving_ratio=float(row.saving_ratio)
                            if row.saving_ratio else None,
                            action_taken=AuditAction.NOTIFICATION_SEND_FAILED,
                            source="scan_and_deliver",
                            dry_run=0,
                        )

                try:
                    session.commit()
                except Exception:
                    log.exception("[deliver-pending] Failed to update notify_status")

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
