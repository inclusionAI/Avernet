"""[编排] Governance admin service — 管理面(§7.5)。

Covers(对 admin_router):
  - Brake (pause/resume) — cross-pod distributed cache
  - pause_ticket — admin pause to waiting_review (§7.5.1)
  - delete_records — admin delete for record_daily / notify_log
  - deliver_pending / deliver_by_worker — manual delivery pipeline
    (_run_delivery 内部实现,原 delivery_runner 已并回)

白名单读写不再经本服务转发:admin_router 直连 GovernanceWhitelistService。

关单能力(admin_close / cancel_pending / close_all_open)已按"工单运营 vs 运维"
边界迁至 :class:`GovernanceWorkflowService`(对应 workflow_router);本服务仅保留
_deliver/pause/resume 等运维职责。审批面(list_review_tickets /
get_review_ticket_detail / review_ticket)亦按路由边界拆至 WorkflowService。

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

from agentclaw.community.core.economy.governance.domain.base import (
    build_delivery_status_json,
)
from agentclaw.community.core.economy.governance.domain.enums import (
    AuditAction,
    CloseReason,
    GovernanceStatus,
    NotifyStatus,
    NotifyType,
)
from agentclaw.community.core.economy.governance.services.service_protocols import (
    GovernanceLifecycleServiceProtocol,
    GovernanceWhitelistServiceProtocol,
)
from agentclaw.community.core.economy.governance.domain.notification import (
    FrozenSnapshot,
    GovernanceNotification,
)
from agentclaw.community.core.economy.governance.domain.record import GovernanceRecord
from agentclaw.community.core.economy.governance.domain.ticket import (
    MutableSnapshot,
)
from agentclaw.community.core.economy.governance.services.notify_render_service import (
    NotifyRenderService,
)
from agentclaw.community.utils.env_utils import get_current_env


if TYPE_CHECKING:
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

_BRAKE_KEY_TEMPLATE = "governance:brake:{env}"
_BRAKE_TTL_SECONDS = 7 * 24 * 3600  # 7 days


# ── Service I/O dataclasses (P5) ─────────────────────────────────────────


@dataclass(frozen=True)
class BrakeState:
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
    """pause_ticket / review_ticket / admin_close 统一返回。"""

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
    """Backend admin operations — admin, bulk ops, review (§7.5)."""

    @inject
    def __init__(
        self,
        cache: CachePlugin,
        whitelist_service: GovernanceWhitelistServiceProtocol,
        notify_repo: NotifyLogRepository,
        audit_repo: GovernanceAuditRepository,
        task_repo: TaskRecordRepository,
        config: Any,  # EconomyGovernanceConfig
        notify_sender: NotifySenderPlugin,
        lifecycle_svc: GovernanceLifecycleServiceProtocol,
        render_svc: NotifyRenderService,
    ) -> None:
        self._cache = cache
        self._whitelist_service = whitelist_service
        self._notify_repo = notify_repo
        self._audit_repo = audit_repo
        self._task_repo = task_repo
        self._config = config
        self._notify_sender = notify_sender
        self._lifecycle_svc = lifecycle_svc
        self._render_svc = render_svc
        self._brake_key = _BRAKE_KEY_TEMPLATE.format(env=get_current_env())

    # -- State queries -------------------------------------------------------

    def is_paused(self) -> bool:
        """Check if the brake is active."""
        try:
            raw = self._cache.get(self._brake_key)
            if raw:
                data = json.loads(raw) if isinstance(raw, str) else raw
                return data.get("action") == "pause"
        except Exception:
            log.warning("[GovernanceAdmin] Failed to read brake state")
        return False

    def get_state(self) -> BrakeState:
        """Query current brake state."""
        paused_info = self._read_pause_info()

        pending_count = self._notify_repo.count_pending()
        open_count = self._notify_repo.count_open_muted()
        whitelist_count = self._whitelist_service.count_by_type()

        return BrakeState(
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
        self._cache.set(self._brake_key, value, ttl=_BRAKE_TTL_SECONDS)

        self._write_admin_audit(
            action_taken=AuditAction.ADMIN_PAUSE,
            actor_id=operator,
            error_msg=f"reason={reason}; operator={operator}",
        )
        log.info(
            "[GovernanceAdmin] Paused by %s: %s", operator, reason,
        )

    def resume(self, reason: str, operator: str) -> None:
        """Resume normal operation. Deletes distributed cache key. Idempotent if not paused."""
        try:
            self._cache.delete(self._brake_key)
        except Exception:
            log.warning("[GovernanceAdmin] Failed to delete brake key, may already be gone")

        self._write_admin_audit(
            action_taken=AuditAction.ADMIN_RESUME,
            actor_id=operator,
            error_msg=f"reason={reason}; operator={operator}",
        )
        log.info(
            "[GovernanceAdmin] Resumed by %s: %s", operator, reason,
        )

    def write_brake_skip_audit(self, *, run_id: str, reason: str) -> None:
        """记录"自动定时 tick 因制动被跳过"的审计(best-effort)。

        Args:
            run_id: 调度层当次 run 标识(用 scan_date 即可)。
            reason: 跳过原因(制动生效)。
        """
        try:
            self._audit_repo.add_audit(
                run_id,
                action_taken=AuditAction.SCAN_SKIPPED_BRAKE,
                source="scheduled_lifecycle",
                error_msg=reason,
                dry_run=0,
            )
        except Exception:
            log.warning(
                "[GovernanceAdmin] Failed to write brake-skip audit, run_id=%s",
                run_id,
            )

    # -- Ticket-level admin operations (§7.5) ----------------------------------

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

        # Advance via driver service (sole driver). Driver orchestrates the
        # OPEN/SCHEDULED → WAITING_REVIEW transition + one-way
        # cancel-pending side effect.
        self._lifecycle_svc.pause_ticket(ticket_id, review_reason="admin_paused")

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

    # -- Records delete (admin) — moved from router ------------------------

    def delete_records(self, body: dict, operator: str) -> dict:
        """Admin delete for record_daily or notify_log.

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
        handles Phase 2-5 only. Phase 3-5 共用 :meth:`_run_delivery`
        (与 :meth:`deliver_by_worker` 同链路,消除重复)。

        ``scan_svc`` / ``skip_scan`` / ``scan_dry_run`` 透传占位
        (router handler 的 Phase 1 cron tick 用,本方法 Phase 2-5 不消费)。
        """
        # ---- Phase 2: Read pending notifications (全量 cron 视角) ----
        pending_rows = self._notify_repo.list_pending_for_cron()
        if max_send and max_send > 0:
            pending_rows = pending_rows[:max_send]

        return self._run_delivery(
            pending_rows,
            override_recipient=override_recipient,
            dry_run=dry_run,
            channel=channel,
            source="scan_and_deliver",
        )

    def deliver_by_worker(
        self,
        *,
        worker_id: str,
        override_recipient: str | None = None,
        dry_run: bool = True,
        channel: str = "auto",
    ) -> dict:
        """按 worker_id 精准投递该工单 pending 通知(不重跑状态机)。

        与 :meth:`deliver_pending` 的差别仅在 Phase 2:前者全量
        ``list_pending_for_cron``,本方法按 worker ``list_pending_by_worker``
        精准过滤。Phase 3-5 build/send/update/audit 共用
        :meth:`_run_delivery`。

        Args:
            worker_id: ``owner_id:bot_id`` 复合键。
            override_recipient: 覆盖收件人;None 时用通知记录的 owner_id。
            dry_run: true 只构建不发钉钉。
            channel: auto(跟随 DB)|markdown|tc_card。

        Returns:
            投递汇总 dict(与 deliver_pending 同 shape)。
        """
        effective_recipient = override_recipient or ""
        pending_rows = self._notify_repo.list_pending_by_worker(worker_id)
        return self._run_delivery(
            pending_rows,
            override_recipient=effective_recipient,
            dry_run=dry_run,
            channel=channel,
            source="deliver_by_worker",
        )

    def _run_delivery(
            self,
            pending_rows: list,
            *,
            override_recipient: str,
            dry_run: bool,
            channel: str,
            source: str,
        ) -> dict:
        """Build → send → update DB + audit(Phase 3-5 共用链路)。

        Args:
            pending_rows: Phase 2 已读的 pending 通知领域模型(全量或按 worker)。
            source: audit source 标记(scan_and_deliver / deliver_by_worker)。
            notify_sender/notify_repo/audit_repo/config/render_svc: 取自 self._*
                (admin_service 注入),无需外部透传。

        Returns:
            投递汇总 dict(total/dry_run/override_recipient/channel/sent_count/results)。
        """
        if not pending_rows:
            return {
                "total": 0,
                "dry_run": dry_run,
                "override_recipient": override_recipient,
                "channel": channel,
                "sent_count": 0,
                "results": [],
            }

        # ---- Phase 4: Build payloads & optionally send ----
        # (Phase 3 dict 中转已删:直接用领域模型 p,渲染经 render_svc 出口)
        results: list[dict] = []
        sent_count = 0
        for p in pending_rows:
            notify_channel = p.channel if channel == "auto" else channel
            # deliver_by_worker 传空 override_recipient 时,逐条按通知 owner 兜底;
            # deliver_pending 透传非空时与之等价(不影响既有 scan-and-deliver 行为)。
            recipient = override_recipient or p.owner_id
            msg_id: str | None = None
            channel_used = notify_channel
            bot_name = p.bot_name or "N/A"

            if notify_channel == "tc_card":
                # 渲染经出口(唯一),build_send_payload 失败返 None → 降级 markdown
                payload = self._render_svc.build_send_payload(p, user_id=recipient, config=self._config)

                if payload is not None:
                    if not dry_run:
                        from agentclaw.community.plugin_api.notify_sender import NotifyMessage
                        msg = NotifyMessage(
                            title="🔔 Bot 治理通知",
                            body=payload.body,
                            recipient=recipient,
                            deep_link=payload.deep_link,
                            extra=payload.extra,
                        )
                        msg_id = self._notify_sender.send(msg, channel="tc_card")

                    if not dry_run and msg_id is None and notify_channel == "tc_card":
                        log.warning(
                            "[DeliverPending] TC card send failed for %s, degrading to Markdown",
                            p.notification_id,
                        )
                        channel_used = "markdown"

                    if not dry_run and channel_used == "markdown":
                        from agentclaw.community.plugin_api.notify_sender import NotifyMessage as _NM
                        msg_md = _NM(
                            title="🔔 Bot 治理通知",
                            body=p.notification_md or "",
                            recipient=recipient,
                        )
                        msg_id = self._notify_sender.send(msg_md, channel="markdown")

                    if dry_run:
                        notification_data = payload.extra.get("notification_data") or {}
                        tc_preview = {
                            "reason_preview": payload.body[:200],
                            "detail_link": payload.deep_link,
                            "notification_data_keys": list(notification_data.keys()),
                        }
                        results.append({
                            "notification_id": p.notification_id,
                            "bot_name": bot_name,
                            "original_recipient": p.owner_id,
                            "sent_to": recipient,
                            "channel": channel_used,
                            "dry_run": True,
                            "tc_card": tc_preview,
                        })
                        continue
                else:
                    # TC card 渲染失败 → 降级 markdown
                    log.warning(
                        "[DeliverPending] TC card build failed for %s, degrading to Markdown",
                        p.notification_id,
                    )
                    channel_used = "markdown"
                    if not dry_run:
                        from agentclaw.community.plugin_api.notify_sender import NotifyMessage as _NM
                        msg_md = _NM(
                            title="🔔 Bot 治理通知",
                            body=p.notification_md or "",
                            recipient=recipient,
                        )
                        msg_id = self._notify_sender.send(msg_md, channel="markdown")

                    if dry_run:
                        results.append({
                            "notification_id": p.notification_id,
                            "bot_name": bot_name,
                            "original_recipient": p.owner_id,
                            "sent_to": recipient,
                            "channel": channel_used,
                            "dry_run": True,
                        })
                        continue
            else:
                if not dry_run:
                    from agentclaw.community.plugin_api.notify_sender import NotifyMessage as _NM
                    msg_plain = _NM(
                        title="🔔 Bot 治理通知",
                        body=p.notification_md or "",
                        recipient=recipient,
                    )
                    msg_id = self._notify_sender.send(msg_plain, channel="markdown")

                if dry_run:
                    results.append({
                        "notification_id": p.notification_id,
                        "bot_name": bot_name,
                        "original_recipient": p.owner_id,
                        "sent_to": recipient,
                        "channel": channel_used,
                        "dry_run": True,
                    })
                    continue

            ok = msg_id is not None
            if ok:
                sent_count += 1
            results.append({
                "notification_id": p.notification_id,
                "bot_name": bot_name,
                "original_recipient": p.owner_id,
                "sent_to": recipient,
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
                # Update ticket delivery_status: JSON {notify_type}:sent (§7.3.5)
                ticket_delivery_status = build_delivery_status_json(
                    p.notify_type.value, NotifyStatus.SENT.value,
                    sent_at=now,
                    external_message_id=r.get("external_message_id"),
                )
                self._task_repo.update_delivery_status(p.ticket_id, ticket_delivery_status)
                self._audit_repo.add_audit(
                    audit_run_id, p.bot_id, p.owner_id,
                    notification_id=nid,
                    check_result=p.decision_at_create,
                    governance_decision=p.decision_at_create,
                    hit_dimensions=p.triggered_dimensions,
                    expected_token_saving=p.estimated_saving_tokens,
                    saving_ratio=p.saving_ratio,
                    action_taken=AuditAction.NOTIFICATION_SENT,
                    source=source,
                    dry_run=0,
                )

            # ---- Failed: update ticket delivery_status + audit ----
            for r in results:
                if r.get("success"):
                    continue
                nid = r["notification_id"]
                p = pending_by_id.get(nid)
                if p is None:
                    continue
                # Update ticket delivery_status: JSON {notify_type}:failed (§7.3.5)
                ticket_delivery_status = build_delivery_status_json(
                    p.notify_type.value, NotifyStatus.FAILED.value,
                    error="delivery failed",
                )
                self._task_repo.update_delivery_status(p.ticket_id, ticket_delivery_status)
                self._audit_repo.add_audit(
                    audit_run_id, p.bot_id, p.owner_id,
                    notification_id=nid,
                    check_result=p.decision_at_create,
                    governance_decision=p.decision_at_create,
                    hit_dimensions=p.triggered_dimensions,
                    expected_token_saving=p.estimated_saving_tokens,
                    saving_ratio=p.saving_ratio,
                    action_taken=AuditAction.NOTIFICATION_SEND_FAILED,
                    source=source,
                    dry_run=0,
                )

        return {
            "total": len(pending_rows),
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
            raw = self._cache.get(self._brake_key)
            if raw:
                return json.loads(raw) if isinstance(raw, str) else raw
        except Exception:
            pass
        return {}

    def _write_admin_audit(
        self,
        *,
        action_taken: str,
        actor_id: str | None = None,
        error_msg: str = "",
    ) -> None:
        """Best-effort audit write for admin operations.

        Delegates to :meth:`GovernanceAuditRepository.add_audit` with the
        admin-scoped ``run_id`` and ``source``.
        """
        try:
            self._audit_repo.add_audit(
                "admin",
                action_taken=action_taken,
                actor_id=actor_id,
                error_msg=error_msg,
                source="admin_api",
                dry_run=0,
            )
        except Exception:
            log.exception("[GovernanceAdmin] Failed to write audit for %s", action_taken)

    # ── Admin remind + force renew (manual ops) ──────────────────────────

    def create_and_send_reminder(
        self, worker_id: str, operator: str,
    ) -> dict:
        """手动补发 reminder:按 worker_id 找 active 工单 → 创建+发送 reminder。

        跳过 remind_at 等待(立 即 create+send,不等 cron tick)。
        无 active 工单 → raise ValueError(400)。
        """
        ticket = self._task_repo.find_active_ticket(worker_id)
        if ticket is None:
            raise ValueError(f"no active ticket for worker_id={worker_id}")

        now = datetime.now()
        run_id = f"admin-remind-{uuid.uuid4().hex[:8]}"
        notification_id = uuid.uuid4().hex
        notification_md = self._render_svc.render_reminder_md(ticket, now=now)

        # Create reminder notify_log
        notify_row = GovernanceNotification.create(
            notification_id=notification_id,
            ticket_id=ticket.ticket_id,
            bot_id=ticket.bot_id,
            bot_name=ticket.bot_name,
            owner_id=ticket.owner_id,
            worker_id=ticket.worker_id,
            snapshot=FrozenSnapshot(
                dt_version=ticket.dt_version,
                decision_at_create=ticket.current_decision or "actionable",
                triggered_dimensions=ticket.triggered_dimensions,
                hit_dimensions_count=ticket.hit_dimensions_count,
                severity=ticket.severity,
                estimated_saving_tokens=ticket.estimated_saving_tokens,
                saving_ratio=ticket.saving_ratio,
                notification_md=notification_md,
                notification_structured=ticket.notification_structured,
            ),
            notify_type=NotifyType.REMINDER,
            notify_source="admin_api",
            channel=getattr(self._config, "notify_channel", "markdown"),
        )
        self._notify_repo.add_notification(notify_row)

        # Send immediately (跳过 cron tick 等待)
        from agentclaw.community.plugin_api.notify_sender import NotifyMessage
        msg = NotifyMessage(
            title="⚠️ 治理通知提醒",
            body=notification_md,
            recipient=ticket.owner_id,
            deep_link="",
            extra={},
        )
        try:
            external_id = self._notify_sender.send(
                msg, channel=getattr(self._config, "notify_channel", "markdown"),
            )
        except Exception:
            log.exception(
                "[GovernanceAdmin] reminder send failed for notification_id=%s",
                notification_id,
            )
            external_id = None

        sent = external_id is not None
        if sent:
            self._notify_repo.update_delivery_status(
                notification_id,
                status=NotifyStatus.SENT,
                external_id=external_id,
            )
            # Update ticket delivery_status for reminder: reminder:sent
            self._task_repo.update_delivery_status(
                ticket.ticket_id,
                build_delivery_status_json(
                    NotifyType.REMINDER.value, NotifyStatus.SENT.value,
                    sent_at=now, external_message_id=external_id,
                ),
            )
        else:
            self._notify_repo.update_delivery_status(
                notification_id, status=NotifyStatus.FAILED,
            )
            # Update ticket delivery_status for reminder: reminder:failed
            self._task_repo.update_delivery_status(
                ticket.ticket_id,
                build_delivery_status_json(
                    NotifyType.REMINDER.value, NotifyStatus.FAILED.value,
                    error="reminder delivery failed",
                ),
            )

        # Advance reminder chain: increment remind_count + set next remind_at
        # 以本次 reminder 为基准推后(同 scan_service _advance_reminder_chain 语义)。
        remind_delays = getattr(self._config, "remind_delays_days", None)
        if remind_delays:
            try:
                delays = [int(d.strip()) for d in str(remind_delays).split(",")]
            except (ValueError, TypeError):
                delays = [3, 7]
        else:
            delays = [3, 7]
        new_count = (ticket.remind_count or 0) + 1
        idx = min(new_count, len(delays) - 1)
        next_delay = delays[idx]
        self._lifecycle_svc.refresh_snapshot(
            ticket.ticket_id,
            remind_at=now + timedelta(days=next_delay),
        )
        # remind_count lives on the ticket entity (not MutableSnapshot),
        # so refresh_snapshot can't set it. Update via task_repo directly.
        self._task_repo.update_remind_count(ticket.ticket_id, new_count)

        # Audit (full: bot_id/owner_id/notification_id/operator)
        self._audit_repo.add_audit(
            f"admin-remind-{uuid.uuid4().hex[:8]}",
            bot_id=ticket.bot_id,
            owner_id=ticket.owner_id,
            notification_id=notification_id,
            actor_id=operator,
            action_taken=AuditAction.REMIND_SENT if sent else AuditAction.REMIND_FAILED,
            source="admin_api",
            error_msg=f"worker_id={worker_id}; sent={sent}",
            dry_run=0,
        )

        log.info(
            "[GovernanceAdmin] Reminder sent by %s: worker=%s, ticket=%s, sent=%s",
            operator, worker_id, ticket.ticket_id, sent,
        )

        return {"notification_id": notification_id, "sent": sent}

    def force_renew_with_record(
        self, record: GovernanceRecord, operator: str,
    ) -> dict:
        """强制换新:用 record 关老(stale_replaced) + 建新 first_send。

        无视 gmt_create 7天 + dt_version guard(admin 手动强制)。
        """
        worker_key = record.worker_id or f"{record.owner_id}:{record.bot_id}"
        now = datetime.now()
        run_id = f"admin-renew-{uuid.uuid4().hex[:8]}"

        # Find + close active ticket (if any)
        active = self._task_repo.find_active_ticket(worker_key)
        if active is not None:
            self._lifecycle_svc.close_for_stale_replace(
                active.ticket_id, now=now,
            )
            self._write_admin_audit(
                action_taken=AuditAction.STALE_REPLACED,
                actor_id=operator,
                error_msg=f"force_renew: old_ticket={active.ticket_id}; worker={worker_key}",
            )

        # Create new ticket + first_send (inline,参照 _create_new_ticket)
        ticket_id = uuid.uuid4().hex
        notification_id = uuid.uuid4().hex

        notification_md = self._render_svc.render_first_notification_md(
            record,
            dt_version=record.dt_version,
            use_reopen_template=False,
            reopen_ref_time=None,
        )

        from agentclaw.community.core.economy.governance.domain.ticket import GovernanceTicket
        ticket_model = GovernanceTicket.create(
            ticket_id=ticket_id,
            worker_id=worker_key,
            bot_id=record.bot_id,
            owner_id=record.owner_id,
            owner_name=record.owner_name,
            bot_name=record.bot_name,
            snapshot=MutableSnapshot(
                dt_version=record.dt_version,
                initial_decision="actionable",
                current_decision="actionable",
                triggered_dimensions=record.hit_dimensions,
                hit_dimensions_count=record.hit_dimensions_count,
                severity=record.governance_max_priority,
                estimated_saving_tokens=record.expected_token_saving,
                saving_ratio=record.saving_ratio,
                token_baseline=record.token_baseline,
                task_summary=record.task_summary,
                notification_structured=record.notification_structured,
                analysis_status=record.analysis_status,
                consecutive_normal_days=0,
                last_decision_dt_version=record.dt_version,
                last_seen_at=now,
                last_sync_at=now,
                delivery_status="none",
            ),
        )
        self._lifecycle_svc.open_ticket(ticket=ticket_model)

        # Create first_send notify_log
        notify_row = GovernanceNotification.create(
            notification_id=notification_id,
            ticket_id=ticket_id,
            bot_id=record.bot_id,
            bot_name=record.bot_name,
            owner_id=record.owner_id,
            worker_id=worker_key,
            snapshot=FrozenSnapshot(
                dt_version=record.dt_version,
                decision_at_create="actionable",
                triggered_dimensions=record.hit_dimensions,
                hit_dimensions_count=record.hit_dimensions_count,
                severity=record.governance_max_priority,
                estimated_saving_tokens=record.expected_token_saving,
                saving_ratio=record.saving_ratio,
                notification_md=notification_md,
                notification_structured=record.notification_structured,
            ),
            notify_type=NotifyType.FIRST_SEND,
            notify_source="admin_api",
            channel=getattr(self._config, "notify_channel", "markdown"),
        )
        self._notify_repo.add_notification(notify_row)
        self._task_repo.update_delivery_status(
            ticket_id,
            build_delivery_status_json(NotifyType.FIRST_SEND.value, "pending"),
        )

        # Audit
        self._audit_repo.add_audit(
            run_id,
            record.bot_id,
            record.owner_id,
            notification_id=notification_id,
            check_result="actionable",
            governance_decision=record.governance_decision,
            hit_dimensions=record.hit_dimensions,
            expected_token_saving=record.expected_token_saving,
            saving_ratio=record.saving_ratio,
            action_taken=AuditAction.ENQUEUED,
            error_msg=f"force_renew by {operator}; old_ticket={active.ticket_id if active else 'none'}",
            source="admin_api",
            dry_run=0,
        )

        log.info(
            "[GovernanceAdmin] Force renew by %s: worker=%s, old=%s, new=%s",
            operator, worker_key,
            active.ticket_id if active else "none", ticket_id,
        )

        return {"ticket_id": ticket_id, "notification_id": notification_id}
