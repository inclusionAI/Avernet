"""Unit tests for governance audit action enums.

Exercises every ``AuditAction`` class constant so all definition lines in
``domain/enums.py`` are executed and verified.
"""
from __future__ import annotations

from agentclaw.community.core.economy.governance.domain.enums import (
    ACTIVE_STATUSES,
    TERMINAL_STATUSES,
    AuditAction,
    CloseReason,
    GovernanceStatus,
)


def test_notification_lifecycle_values():
    assert AuditAction.NOTIFICATION_CREATED == "notification_created"
    assert AuditAction.NOTIFICATION_SENT == "notification_sent"
    assert AuditAction.NOTIFICATION_SEND_FAILED == "notification_send_failed"
    assert AuditAction.REMIND_SENT == "remind_sent"
    assert AuditAction.REMIND_FAILED == "remind_failed"


def test_user_feedback_values():
    assert AuditAction.USER_OPTIMIZED == "user_optimized"
    assert AuditAction.USER_NEED_TIME == "user_need_time"
    assert AuditAction.USER_DISPUTE == "user_dispute"
    assert AuditAction.USER_WHITELIST == "user_whitelisted"
    assert AuditAction.USER_PENDING_FEEDBACK == "user_pending_feedback"


def test_system_auto_values():
    assert AuditAction.SYSTEM_RESOLVED == "system_resolved"
    assert AuditAction.EXPIRED == "expired"
    assert AuditAction.MUTE_EXPIRED == "mute_expired"
    assert AuditAction.OUT_OF_SCOPE == "out_of_scope"


def test_scan_skip_values():
    assert AuditAction.SCAN_SKIP_NOT_READY == "scan_skip_not_ready"
    assert AuditAction.SCAN_WHITELISTED == "scan_whitelisted"
    assert AuditAction.SCAN_SKIP_MUTED == "scan_skip_muted"
    assert AuditAction.SCAN_SKIP_COOLDOWN == "scan_skip_cooldown"


def test_close_reason_values():
    assert CloseReason.ADMIN_CLOSED == "admin_closed"
    assert CloseReason.SCAN_WHITELISTED == "scan_whitelisted"
    assert CloseReason.WHITELIST_APPROVED == "whitelist_approved"
    assert CloseReason.USER_OPTIMIZED_APPROVED == "user_optimized_approved"
    assert CloseReason.REVIEW_REJECTED == "review_rejected"
    assert CloseReason.STALE_REPLACED == "stale_replaced"


def test_admin_action_values():
    assert AuditAction.ADMIN_CANCEL_PENDING == "admin_cancel_pending"
    assert AuditAction.ADMIN_CLOSE_ALL == "admin_close_all"
    assert AuditAction.ADMIN_PAUSE == "admin_pause"
    assert AuditAction.ADMIN_RESUME == "admin_resume"
    assert AuditAction.ADMIN_WHITELIST == "admin_whitelisted"
    assert AuditAction.RECORDS_DELETED == "records_deleted"
    assert AuditAction.NOTIFICATIONS_DELETED == "notifications_deleted"


def test_whitelist_management_values():
    """白名单管理类审计动作(含观察刷新 WHITELIST_OBSERVED)。

    WHITELIST_OBSERVED = off-batch 刷 OBSERVED 单快照(持续观察,不发通知);
    与 SCAN_WHITELISTED(scan 兜底关残留/记加白命中)区分。
    """
    assert AuditAction.WHITELIST_REMOVED == "whitelist_removed"
    assert AuditAction.WHITELIST_OBSERVED == "whitelist_observed"


def test_all_action_values_are_unique_strings():
    values = [
        v
        for k, v in vars(AuditAction).items()
        if not k.startswith("_") and isinstance(v, str)
    ]
    assert len(values) == 56
    assert all(isinstance(v, str) for v in values)
    assert len(set(values)) == len(values)


def test_status_constants():
    """状态族常量是 ticket 侧 governance_status 谓词的公共来源。

    Step1 基线:ACTIVE_STATUSES = 三活跃态;TERMINAL_STATUSES = {CLOSED}。
    二者无交集(同一工单不会同时属于活跃与终态)。Step2 扩 TERMINAL 含 OBSERVED
    时,本测试需同步更新(OBSERVED 必须 ∈ TERMINAL、∉ ACTIVE)。
    """
    assert GovernanceStatus.OPEN in ACTIVE_STATUSES
    assert GovernanceStatus.SCHEDULED in ACTIVE_STATUSES
    assert GovernanceStatus.WAITING_REVIEW in ACTIVE_STATUSES
    assert GovernanceStatus.CLOSED not in ACTIVE_STATUSES

    assert GovernanceStatus.CLOSED in TERMINAL_STATUSES
    assert GovernanceStatus.OPEN not in TERMINAL_STATUSES

    # 活跃与终态互斥(状态机基本不变式)
    assert ACTIVE_STATUSES.isdisjoint(TERMINAL_STATUSES)

    # frozenset 不可变,防误改
    assert isinstance(ACTIVE_STATUSES, frozenset)
    assert isinstance(TERMINAL_STATUSES, frozenset)
