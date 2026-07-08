"""Unit tests for governance audit action enums.

Exercises every ``AuditAction`` class constant and the ``LEGACY_ACTION_MAP``
so all definition lines in ``contracts/enums.py`` are executed and verified.
"""
from __future__ import annotations

from agentclaw.community.core.economy.governance.contracts.enums import (
    LEGACY_ACTION_MAP,
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
    assert len(values) == 51
    assert all(isinstance(v, str) for v in values)
    assert len(set(values)) == len(values)


def test_legacy_action_map_maps_old_to_canonical():
    assert LEGACY_ACTION_MAP["enqueued"] == AuditAction.NOTIFICATION_CREATED
    assert LEGACY_ACTION_MAP["first_delivered"] == AuditAction.NOTIFICATION_SENT
    assert LEGACY_ACTION_MAP["first_delivery_failed"] == AuditAction.NOTIFICATION_SEND_FAILED
    assert LEGACY_ACTION_MAP["reminded"] == AuditAction.REMIND_SENT
    assert LEGACY_ACTION_MAP["feedback_optimized"] == AuditAction.USER_OPTIMIZED
    assert LEGACY_ACTION_MAP["feedback_need_time"] == AuditAction.USER_NEED_TIME
    assert LEGACY_ACTION_MAP["feedback_dispute"] == AuditAction.USER_DISPUTE
    assert LEGACY_ACTION_MAP["feedback_whitelist"] == AuditAction.USER_WHITELIST
    assert LEGACY_ACTION_MAP["auto_resolved"] == AuditAction.SYSTEM_RESOLVED
    assert LEGACY_ACTION_MAP["expired_unresolved"] == AuditAction.EXPIRED
    assert LEGACY_ACTION_MAP["data_not_ready"] == AuditAction.SCAN_SKIP_NOT_READY
    assert LEGACY_ACTION_MAP["whitelist_filtered"] == AuditAction.SCAN_SKIP_WHITELIST
    assert LEGACY_ACTION_MAP["muted"] == AuditAction.SCAN_SKIP_MUTED
    assert LEGACY_ACTION_MAP["cooldown_filtered"] == AuditAction.SCAN_SKIP_COOLDOWN
    assert LEGACY_ACTION_MAP["emergency_cancelled"] == AuditAction.ADMIN_CANCEL_PENDING
    assert LEGACY_ACTION_MAP["admin_closed_all"] == AuditAction.ADMIN_CLOSE_ALL
    assert LEGACY_ACTION_MAP["emergency_paused"] == AuditAction.ADMIN_PAUSE
    assert LEGACY_ACTION_MAP["emergency_resumed"] == AuditAction.ADMIN_RESUME
    assert LEGACY_ACTION_MAP["emergency_whitelisted"] == AuditAction.ADMIN_WHITELIST


def test_legacy_action_map_is_complete_and_typed():
    assert isinstance(LEGACY_ACTION_MAP, dict)
    assert len(LEGACY_ACTION_MAP) == 19
    # Every legacy value maps onto a canonical AuditAction string.
    canonical = {
        v
        for k, v in vars(AuditAction).items()
        if not k.startswith("_") and isinstance(v, str)
    }
    assert set(LEGACY_ACTION_MAP.values()) <= canonical
