"""Unit tests for governance audit action enums.

Exercises every ``AuditAction`` class constant so all definition lines in
``domain/enums.py`` are executed and verified.
"""
from __future__ import annotations

from agentclaw.community.core.economy.governance.domain.enums import (
    AuditAction,
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
    assert AuditAction.SCAN_SKIP_WHITELIST == "scan_skip_whitelist"
    assert AuditAction.SCAN_SKIP_MUTED == "scan_skip_muted"
    assert AuditAction.SCAN_SKIP_COOLDOWN == "scan_skip_cooldown"


def test_admin_emergency_values():
    assert AuditAction.ADMIN_CANCEL_PENDING == "admin_cancel_pending"
    assert AuditAction.ADMIN_CLOSE_ALL == "admin_close_all"
    assert AuditAction.ADMIN_PAUSE == "admin_pause"
    assert AuditAction.ADMIN_RESUME == "admin_resume"
    assert AuditAction.ADMIN_WHITELIST == "admin_whitelisted"
    assert AuditAction.RECORDS_DELETED == "records_deleted"
    assert AuditAction.NOTIFICATIONS_DELETED == "notifications_deleted"


def test_all_action_values_are_unique_strings():
    values = [
        v
        for k, v in vars(AuditAction).items()
        if not k.startswith("_") and isinstance(v, str)
    ]
    assert len(values) == 52
    assert all(isinstance(v, str) for v in values)
    assert len(set(values)) == len(values)
