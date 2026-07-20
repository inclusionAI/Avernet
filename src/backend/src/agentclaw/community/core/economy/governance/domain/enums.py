"""Canonical enumerations for the economy/governance module.

All enums inherit ``(str, Enum)`` so that:
  - comparison with raw strings works (``GovernanceStatus.OPEN == "open"`` → True)
  - ORM columns storing ``str`` values accept enum members directly
  - ``str(member)`` returns the value string (via ``__str__``)

Naming convention for AuditAction: ``{subject}_{verb}`` — who did what.
"""
from __future__ import annotations

from enum import Enum


class AuditAction(str, Enum):
    """Canonical action_taken values for ac_governance_audit.

    Converted from plain class constants to ``str, Enum`` for type safety
    while maintaining full backward compatibility with the existing
    ``action_taken: str`` column type.
    """

    def __str__(self) -> str:
        return self.value

    # ── Notification lifecycle ────────────────────────
    NOTIFICATION_CREATED = "notification_created"           # Notification row created (was: enqueued)
    NOTIFICATION_SENT = "notification_sent"                 # First delivery succeeded (was: first_delivered)
    NOTIFICATION_SEND_FAILED = "notification_send_failed"   # Delivery failed (was: first_delivery_failed)
    REMIND_SENT = "remind_sent"                             # Reminder sent (was: reminded)
    REMIND_FAILED = "remind_failed"                         # Reminder send failed (unchanged)
    REMIND_SCHEDULED = "remind_scheduled"                   # Reminder notify created (not yet sent)

    # ── User feedback ─────────────────────────────────
    USER_OPTIMIZED = "user_optimized"           # User optimized → waiting_review (was: feedback_optimized)
    USER_NEED_TIME = "user_need_time"           # User needs time → scheduled (was: feedback_need_time)
    USER_DISPUTE = "user_dispute"               # User dispute → waiting_review (was: feedback_dispute)
    USER_WHITELIST = "user_whitelisted"         # User whitelisted → waiting_review (was: feedback_whitelist)
    USER_PENDING_FEEDBACK = "user_pending_feedback"  # Card shown, awaiting feedback (new)
    FEEDBACK_DUPLICATE_IGNORED = "feedback_duplicate_ignored"  # 重复反馈被忽略 (§7.4.1)
    FEEDBACK_TERMINAL_IGNORED = "feedback_terminal_ignored"    # 终态工单反馈被忽略 (§7.4.1)

    # ── System auto ───────────────────────────────────
    SYSTEM_RESOLVED = "system_resolved"         # Consecutive normal days → closed (was: auto_resolved)
    EXPIRED = "expired"                         # No-response expired (was: expired_unresolved)
    MUTE_EXPIRED = "mute_expired"               # Mute period expired, still actionable (unchanged)
    OUT_OF_SCOPE = "out_of_scope"               # Data no longer in scope → closed (unchanged)

    # ── Scan skip ─────────────────────────────────────
    SCAN_SKIP_NOT_READY = "scan_skip_not_ready"     # Data not ready (was: data_not_ready)
    SCAN_WHITELISTED = "scan_whitelisted"   # scan 遇到白名单 bot: 清理残留活跃单或无单跳过 (只记动作, 不猜原因; 合并原 scan_skip_whitelist + whitelist_closed)
    SCAN_SKIP_MUTED = "scan_skip_muted"             # Already in mute period (was: muted)
    SCAN_SKIP_COOLDOWN = "scan_skip_cooldown"       # Cooldown period (was: cooldown_filtered)

    # ── Admin actions ────────────────────────────────
    ADMIN_CANCEL_PENDING = "admin_cancel_pending"   # Cancel pending
    ADMIN_CLOSE_ALL = "admin_close_all"             # Close all open/muted (was: admin_closed_all)
    ADMIN_PAUSE = "admin_pause"                     # Admin pause
    ADMIN_RESUME = "admin_resume"                   # Admin resume
    ADMIN_WHITELIST = "admin_whitelisted"           # Admin whitelist
    STALE_REPLACED = "stale_replaced"               # 未回复换新:关老工单用新数据重建
    SCAN_SKIPPED_BRAKE = "scan_skipped_brake"       # 自动定时 tick 因制动被跳过 (调度层判定)

    # ── Whitelist management (new) ──────────────────
    WHITELIST_REMOVED = "whitelist_removed"             # 管理员删除白名单条目
    WHITELIST_OBSERVED = "whitelist_observed"           # 白名单 bot 观察刷新(off-batch 刷 OBSERVED 单快照,不发通知)

    # ── Task record / ticket lifecycle (new) ──────────
    ENQUEUED = "enqueued"                           # 新工单+first_send notify 创建 (§7.1.4)
    COOLDOWN_FILTERED = "cooldown_filtered"         # cooldown 期内跳过建单 (§7.1.4 Step 5)
    STILL_ACTIONABLE = "still_actionable"           # 仍有 active 工单, 刷新快照 (§7.1.4 Step 4)
    AUTO_SILENCED = "auto_silenced"                 # 不在治理范围, 自动静默 (§7.2.6)
    AUTO_SILENCE_CONVERGED = "auto_silence_converged"  # 连续 N 天 normal, 收敛关闭 (§7.2.6)
    AUTO_SILENCE_RESUMED = "auto_silence_resumed"   # 从 normal 恢复 actionable, 恢复提醒 (§7.1.4)
    SCHEDULE_DUE = "schedule_due"                   # 排期观察到期 → waiting_review (§7.3.4)
    PAUSED_FOR_REVIEW = "paused_for_review"         # 管理员暂停 → waiting_review (§7.5.1)

    # ── Admin review (new) ────────────────────────────
    REVIEW_APPROVE_CLOSE = "review_approve_close"       # 管理员审核通过关闭 (§7.5.2)
    REVIEW_APPROVE_SCHEDULED = "review_approve_scheduled"  # 管理员审核同意排期 → scheduled (§7.5.2)
    REVIEW_APPROVE_WHITELIST = "review_approve_whitelist"  # 管理员审核通过加白 (§7.5.2)
    REVIEW_REJECT_FOR_REOPEN = "review_reject_for_reopen"  # 管理员打回, 释放 active_worker (§7.5.2)

    # ── Notify delivery (new) ────────────────────────
    NOTIFY_CANCELLED_NOT_ACTIONABLE = "notify_cancelled_not_actionable"  # open+非actionable 取消 pending
    NOTIFY_CANCELLED_NON_OPEN = "notify_cancelled_non_open"              # 非open 取消 pending
    NOTIFY_FAILED_TERMINAL = "notify_failed_terminal"                    # 达到 max_send_attempts 终态失败
    BATCH_QUALITY_SKIP_SILENCE = "batch_quality_skip_silence"            # 数据质量校验失败, 跳过自动静默

    # ── Admin delete ───────────────────────────────────
    RECORDS_DELETED = "records_deleted"                     # 管理员删除 task_record 行
    NOTIFICATIONS_DELETED = "notifications_deleted"         # 管理员删除 notify_log 行
    TICKET_CASCADE_PURGED = "ticket_cascade_purged"         # 管理员按 ticket_id 级联删工单+归属通知

    # ── Point-to-point delivery (manual testing tool) ────
    POINT_TO_POINT_NOTIFY_CREATED = "point_to_point_notify_created"      # p2p 自动创建了通知
    POINT_TO_POINT_DELIVERED = "point_to_point_delivered"                 # p2p 发送成功
    POINT_TO_POINT_DELIVERY_FAILED = "point_to_point_delivery_failed"     # p2p 发送失败
    POINT_TO_POINT_TICKET_NOT_FOUND = "point_to_point_ticket_not_found"   # p2p 未找到活跃工单
    POINT_TO_POINT_TICKET_NOT_OPEN = "point_to_point_ticket_not_open"     # p2p 工单不在 open 态
    POINT_TO_POINT_NOT_ACTIONABLE = "point_to_point_not_actionable"       # p2p 工单 latest_decision 非 actionable


class GovernanceStatus(str, Enum):
    """工单/治理状态 — 用于 GovernanceTicket.governance_status.

    OBSERVED = 白名单观察态:bot 进入治理白名单后,其工单转 OBSERVED 而非
    CLOSED,由后续 offline-batch 持续刷新快照供评审观察,但**不发通知、不占
    治理人力**。归终态族(ACTIVE_STATUSES 不含它),故 ``find_active_ticket``
    天然不命中 → delivery/admin 不操作观察单。删白后 OBSERVED → CLOSED 收尾。
    """

    OPEN = "open"
    SCHEDULED = "scheduled"
    WAITING_REVIEW = "waiting_review"
    OBSERVED = "observed"
    CLOSED = "closed"

    def __str__(self) -> str:
        return self.value


# ── ticket 侧 governance_status 谓词的公共来源 ──────────────────────────
# 收口判据:多态集合查询(语义集合,会因加态而变,散落重复)引这些常量;单态
# 精确查询(== 某态,加态不影响它)只换枚举不引常量。
#
# 为什么存在:加新状态(如 OBSERVED)时,只需改这里一处,所有"活跃态/终态"
# 集合消费方自动同步,避免散落在 repo 各处的 in_(...) 谓词逐一改、漏一处
# 即静默 bug。
#
# 范围:仅用于 ticket 侧(``GovernanceTicketOrm.governance_status``)。
# 通知表 ``GovernanceNotificationOrm`` 也有同名列 governance_status,但语义
# 不同(建通知时工单状态快照),通知侧谓词不引此常量,避免两个不同概念混着改。
ACTIVE_STATUSES: frozenset[GovernanceStatus] = frozenset({
    GovernanceStatus.OPEN,
    GovernanceStatus.SCHEDULED,
    GovernanceStatus.WAITING_REVIEW,
})
"""活跃态集合 — 工单仍在治理链路中(可投递/可刷新/可 review)。
Step1 不含 OBSERVED;Step2 加 OBSERVED 后仍不含(观察态归终态族,
不发通知、不被 find_active_ticket 命中)。"""

TERMINAL_STATUSES: frozenset[GovernanceStatus] = frozenset({
    GovernanceStatus.CLOSED,
    GovernanceStatus.OBSERVED,
})
"""终态族 — 工单生命周期结束(不再进 active 治理链路)。OBSERVED 归终态族:
持续刷新快照供评审观察,但不发通知、不占治理人力、不被 find_active_ticket
命中。"""


class NotifyStatus(str, Enum):
    """通知投递状态 — 用于 GovernanceNotification.notify_status."""

    PENDING = "pending"
    SENDING = "sending"
    SENT = "sent"
    FAILED = "failed"
    CANCELLED = "cancelled"

    def __str__(self) -> str:
        return self.value


class NotifyType(str, Enum):
    """通知类型 — 用于 GovernanceNotification.notify_type."""

    FIRST_SEND = "first_send"
    REMINDER = "reminder"

    def __str__(self) -> str:
        return self.value


class CloseReason(str, Enum):
    """工单关闭原因 — 用于 GovernanceTicket.close_reason."""

    ADMIN_CLOSED = "admin_closed"
    AUTO_SILENCED_NORMAL = "auto_silenced_normal"
    SCAN_WHITELISTED = "scan_whitelisted"          # scan 清理白名单 bot 残留活跃单 (只记动作, 不猜原因)
    WHITELIST_APPROVED = "whitelist_approved"      # owner 申请加白 → admin 审阅同意关单 (source=admin_review)
    USER_OPTIMIZED_APPROVED = "user_optimized_approved"
    REVIEW_REJECTED = "review_rejected"
    STALE_REPLACED = "stale_replaced"

    def __str__(self) -> str:
        return self.value


class Response(str, Enum):
    """用户反馈类型 — 用于 GovernanceTicket.response."""

    OPTIMIZED = "optimized"
    NEED_TIME = "need_time"
    DISPUTE = "dispute"
    WHITELIST = "whitelist"

    def __str__(self) -> str:
        return self.value


class TicketAction(str, Enum):
    """管理员 review 动作 — 对用户反馈的裁决(§7.5.2 + need_time 待审扩展)。

    按用户反馈类型分发不同"同意"动作,驳回通用。加白(approve_whitelist)是 whitelist
    反馈的同意裁决(与运维独立一键加白 /admin/whitelist 出发点不同,并存)。
    """

    APPROVE_CLOSE = "approve_close"            # 同意(optimized/dispute)→ closed
    APPROVE_SCHEDULED = "approve_scheduled"    # 同意(need_time)→ scheduled [新]
    APPROVE_WHITELIST = "approve_whitelist"    # 同意(whitelist)→ 加白 + closed
    REJECT_FOR_REOPEN = "reject_for_reopen"    # 驳回(通用)→ closed(review_rejected, scan 重建)

    def __str__(self) -> str:
        return self.value


# Convenience sets for common status checks
ACTIVE_STATUSES: frozenset[GovernanceStatus] = frozenset({
    GovernanceStatus.OPEN, GovernanceStatus.SCHEDULED, GovernanceStatus.WAITING_REVIEW,
})