"""
Tests for WorkerAuditLog

Stage 1 Domain Model Tests
"""

import pytest
from datetime import datetime

from src.domain.models.worker_audit_log import WorkerAuditLog, WorkerAuditAction
from src.domain.models.worker_source_info import WorkerSourceType


class TestWorkerAuditAction:
    """WorkerAuditAction 枚举测试"""

    def test_audit_action_values(self):
        """测试审计动作值"""
        assert WorkerAuditAction.CREATED.value == "created"
        assert WorkerAuditAction.UPDATED.value == "updated"
        assert WorkerAuditAction.LIFECYCLE_CHANGED.value == "lifecycle_changed"
        assert WorkerAuditAction.RUNTIME_STATE_CHANGED.value == "runtime_state_changed"
        assert WorkerAuditAction.DELETED.value == "deleted"

    def test_audit_action_count(self):
        """测试审计动作数量"""
        assert len(WorkerAuditAction) == 6


class TestWorkerAuditLog:
    """WorkerAuditLog 模型测试"""

    def test_audit_log_creation_minimal(self):
        """测试最小创建"""
        log = WorkerAuditLog(
            worker_id="wrk_001",
            action=WorkerAuditAction.CREATED,
        )
        assert log.worker_id == "wrk_001"
        assert log.action == WorkerAuditAction.CREATED
        assert log.id.startswith("audit_")
        assert log.performed_at is not None

    def test_audit_log_creation_full(self):
        """测试完整创建"""
        log = WorkerAuditLog(
            worker_id="wrk_001",
            action=WorkerAuditAction.RUNTIME_STATE_CHANGED,
            old_value="offline",
            new_value="online",
            source_type=WorkerSourceType.API,
            source_ref="api://request",
            performed_by="user_123",
        )
        assert log.worker_id == "wrk_001"
        assert log.action == WorkerAuditAction.RUNTIME_STATE_CHANGED
        assert log.old_value == "offline"
        assert log.new_value == "online"
        assert log.source_type == WorkerSourceType.API
        assert log.source_ref == "api://request"
        assert log.performed_by == "user_123"

    def test_audit_log_auto_id(self):
        """测试自动生成 ID"""
        log1 = WorkerAuditLog(worker_id="wrk_001", action=WorkerAuditAction.CREATED)
        log2 = WorkerAuditLog(worker_id="wrk_002", action=WorkerAuditAction.CREATED)
        assert log1.id != log2.id
        assert log1.id.startswith("audit_")
        assert log2.id.startswith("audit_")

    def test_audit_log_auto_timestamp(self):
        """测试自动设置时间戳"""
        before = datetime.utcnow()
        log = WorkerAuditLog(worker_id="wrk_001", action=WorkerAuditAction.CREATED)
        after = datetime.utcnow()

        assert before <= log.performed_at <= after

    def test_audit_log_default_source_type(self):
        """测试默认来源类型"""
        log = WorkerAuditLog(worker_id="wrk_001", action=WorkerAuditAction.CREATED)
        assert log.source_type == WorkerSourceType.API

    def test_audit_log_forbid_extra_fields(self):
        """测试禁止额外字段"""
        with pytest.raises(Exception):
            WorkerAuditLog(
                worker_id="wrk_001",
                action=WorkerAuditAction.CREATED,
                unknown_field="value",
            )