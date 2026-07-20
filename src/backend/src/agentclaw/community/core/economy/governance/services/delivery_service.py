"""[编排] Governance delivery service — 投递编排域。

从 :class:`GovernanceAdminService` 按职责边界抽出的投递编排服务:
把 pending/scheduled 通知按 channel 规则实际投递 + 回写 notify_status /
delivery_status + 写投递审计。

依赖边界:
  - 上行(web):admin_router 注入 GovernanceDeliveryServiceProtocol。
  - 下行(repo):notify_repo(list/claim/mark_sent/mark_failed)、
    task_repo(delivery_status 回写)、audit_repo(投递审计)。
  - 横向(plugin/service):notify_sender(实际发送)、render_svc(渲染正文)、
    lifecycle_svc(reminder 链推进:refresh_snapshot 更新 remind_count/remind_at)。
    不反向依赖 admin_service。

本服务由 admin-service-split-delivery SDD 抽出(Task 2/3 填充方法);Task 1 仅
搭空壳 + protocol + DI 绑定,不改任何调用方行为。
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from injector import inject

from agentclaw.community.plugin_api.notify_sender import NotifyMessage

from agentclaw.community.core.economy.governance.domain.enums import (
    AuditAction,
    NotifyStatus,
    NotifyType,
)
from agentclaw.community.core.economy.governance.domain.notification import (
    FrozenSnapshot,
    GovernanceNotification,
)
from agentclaw.community.core.economy.governance.services.service_protocols import (
    GovernanceLifecycleServiceProtocol,
)
from agentclaw.community.log import get_logger

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
    from agentclaw.community.core.economy.governance.services.notify_render_service import (
        NotifyRenderService,
    )
    from agentclaw.community.plugin_api.notify_sender import NotifySenderPlugin


log = get_logger(__name__)


class GovernanceDeliveryService:
    """投递编排服务 — deliver_pending / deliver_by_worker / create_and_send_reminder。

    承接自 GovernanceAdminService 的投递域(原 deliver_pending/deliver_by_worker/
    _run_delivery/create_and_send_reminder),依赖 notify_sender + render_svc +
    lifecycle_svc。方法体由 Task 2/3 从 admin_service 原样迁入(行为零回归)。
    """

    @inject
    def __init__(
        self,
        notify_repo: NotifyLogRepository,
        audit_repo: GovernanceAuditRepository,
        task_repo: TaskRecordRepository,
        config: Any,  # EconomyGovernanceConfig
        notify_sender: NotifySenderPlugin,
        render_svc: NotifyRenderService,
        lifecycle_svc: GovernanceLifecycleServiceProtocol,
    ) -> None:
        self._notify_repo = notify_repo
        self._audit_repo = audit_repo
        self._task_repo = task_repo
        self._config = config
        self._notify_sender = notify_sender
        self._render_svc = render_svc
        self._lifecycle_svc = lifecycle_svc

    # ── 投递编排(Task 2 迁入) ──────────────────────────────────────

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
                (投递服务注入),无需外部透传。

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
                        msg_md = NotifyMessage(
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
                        msg_md = NotifyMessage(
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
                    msg_plain = NotifyMessage(
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
                # Update ticket delivery_status: sent (§7.3.5)
                self._task_repo.update_delivery_status(
                    p.ticket_id,
                    NotifyStatus.SENT.value,
                )
                # 投递成功刷 last_notified_at
                self._task_repo.update_last_notified_at(p.ticket_id, now)
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
                # Update ticket delivery_status: failed (§7.3.5)
                self._task_repo.update_delivery_status(
                    p.ticket_id,
                    NotifyStatus.FAILED.value,
                )
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
    # ── reminder(Task 3 迁入) ─────────────────────────────────────

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
            # Update ticket delivery_status for reminder: sent
            self._task_repo.update_delivery_status(
                ticket.ticket_id,
                NotifyStatus.SENT.value,
            )
            # 投递成功刷 last_notified_at
            self._task_repo.update_last_notified_at(ticket.ticket_id, now)
        else:
            self._notify_repo.update_delivery_status(
                notification_id, status=NotifyStatus.FAILED,
            )
            # Update ticket delivery_status for reminder: failed
            self._task_repo.update_delivery_status(
                ticket.ticket_id,
                NotifyStatus.FAILED.value,
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
            run_id,
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
