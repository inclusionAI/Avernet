"""[辅助] Delivery runner — 投递链路 Phase 3-5(build → send → update DB + audit)。

从 GovernanceAdminService._run_delivery 抽出(admin-router-regroup Task 9):该链路
被 deliver_pending(scan_and_deliver 全量)与 deliver_by_worker(按 worker 精准)复用,
抽成模块级函数以单一职责 + 避免宿主 service 超 R9 1000 行 cap。

依赖(notify_sender / notify_repo / audit_repo / config)经参数传入,不持状态。
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from agentclaw.community.core.economy.governance.domain.enums import (
    AuditAction,
    NotifyStatus,
)
from agentclaw.community.log import get_logger

if TYPE_CHECKING:
    from agentclaw.community.core.economy.governance.domain.notification import (
        GovernanceNotification,
    )


log = get_logger(__name__)


def run_delivery(
    pending_rows: list[GovernanceNotification],
    *,
    override_recipient: str,
    dry_run: bool,
    channel: str,
    source: str,
    notify_sender: Any,
    notify_repo: Any,
    audit_repo: Any,
    config: Any,
) -> dict:
    """Build → send → update DB + audit(Phase 3-5 共用链路)。

    Args:
        pending_rows: Phase 2 已读的 pending 通知领域模型(全量或按 worker)。
        source: audit source 标记(scan_and_deliver / deliver_by_worker)。
        notify_sender: NotifySenderPlugin — 实际发钉钉。
        notify_repo: NotifyLogRepository — 更新 notify_status。
        audit_repo: GovernanceAuditRepository — 写投递审计。
        config: EconomyGovernanceConfig — tc_card_id / preview_url / iframe_callback_url。

    Returns:
        投递汇总 dict(total/dry_run/override_recipient/channel/sent_count/results)。
    """
    from agentclaw.community.core.economy.governance.services.notify_builder_service import (
        build_card_notification_data,
        build_governance_reason,
        build_tc_card_detail_link,
    )

    if not pending_rows:
        return {
            "total": 0,
            "dry_run": dry_run,
            "override_recipient": override_recipient,
            "channel": channel,
            "sent_count": 0,
            "results": [],
        }

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
        # deliver_by_worker 传空 override_recipient 时,逐条按通知 owner 兜底;
        # deliver_pending 透传非空时与之等价(不影响既有 scan-and-deliver 行为)。
        recipient = override_recipient or d["original_recipient"]
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
                    card_id=config.tc_card_id,
                    notification_data=notification_data,
                    base_url=config.tc_card_preview_url,
                    iframe_callback_url=config.iframe_callback_url,
                    staff_id=recipient,
                )
                tc_card_extra = {
                    "bot_id": d["bot_id"],
                    "card_id": config.tc_card_id,
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
                    recipient=recipient,
                    deep_link=detail_link,
                    extra=tc_card_extra,
                )
                msg_id = notify_sender.send(msg, channel="tc_card")

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
                    recipient=recipient,
                )
                msg_id = notify_sender.send(msg_md, channel="markdown")

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
                    "sent_to": recipient,
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
                    recipient=recipient,
                )
                msg_id = notify_sender.send(msg_plain, channel="markdown")

            if dry_run:
                results.append({
                    "notification_id": d["notification_id"],
                    "bot_name": d["bot_name"],
                    "original_recipient": d["original_recipient"],
                    "sent_to": recipient,
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
            notify_repo.update_delivery_status(
                nid,
                status=NotifyStatus.SENT,
                external_id=r.get("external_message_id"),
                at=now,
                channel=result_channel if result_channel and result_channel != original_channel else None,
            )
            audit_repo.add_audit(
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

        # ---- Failed: audit only ----
        for r in results:
            if r.get("success"):
                continue
            nid = r["notification_id"]
            p = pending_by_id.get(nid)
            if p is None:
                continue
            audit_repo.add_audit(
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
        "total": len(deliveries),
        "dry_run": dry_run,
        "override_recipient": override_recipient,
        "channel": channel,
        "sent_count": sent_count,
        "results": results,
    }
