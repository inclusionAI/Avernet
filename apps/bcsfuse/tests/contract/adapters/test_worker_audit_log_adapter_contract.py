"""
Contract Tests for WorkerAuditLogAdapter

验证所有实现（InMemory / SQLite）都符合 WorkerAuditLogAdapter 协议。
"""

import pytest

from tests.contract.adapters.helpers import WorkerAuditLogAdapterContractTests
from src.infra.adapters.in_memory_worker_audit_log_store import InMemoryWorkerAuditLogStore


# ============================================================================
# InMemory Implementation Tests
# ============================================================================

@pytest.fixture
def in_memory_audit_log_adapter():
    """创建 InMemory WorkerAuditLogStore 实例"""
    return InMemoryWorkerAuditLogStore()


class TestInMemoryWorkerAuditLogStore:
    """InMemory 实现的契约测试"""

    def test_append_log(self, in_memory_audit_log_adapter):
        """测试追加日志"""
        WorkerAuditLogAdapterContractTests(in_memory_audit_log_adapter).test_append_log()

    def test_list_logs_by_worker(self, in_memory_audit_log_adapter):
        """测试按 Worker ID 查询日志"""
        WorkerAuditLogAdapterContractTests(in_memory_audit_log_adapter).test_list_logs_by_worker()

    def test_list_logs_by_actions(self, in_memory_audit_log_adapter):
        """测试按动作类型过滤日志"""
        WorkerAuditLogAdapterContractTests(in_memory_audit_log_adapter).test_list_logs_by_actions()

    def test_list_logs_pagination(self, in_memory_audit_log_adapter):
        """测试日志分页"""
        WorkerAuditLogAdapterContractTests(in_memory_audit_log_adapter).test_list_logs_pagination()

    def test_get_latest_log(self, in_memory_audit_log_adapter):
        """测试获取最新日志"""
        WorkerAuditLogAdapterContractTests(in_memory_audit_log_adapter).test_get_latest_log()

    def test_get_latest_log_not_found(self, in_memory_audit_log_adapter):
        """测试获取不存在的最新日志"""
        WorkerAuditLogAdapterContractTests(in_memory_audit_log_adapter).test_get_latest_log_not_found()


# ============================================================================
# SQLite Implementation Tests
# ============================================================================

@pytest.fixture
def sqlite_audit_log_adapter():
    """创建 SQLite WorkerAuditLogStore 实例"""
    from src.infra.adapters.sqlite_worker_audit_log_store import SQLiteWorkerAuditLogStore
    return SQLiteWorkerAuditLogStore(":memory:")


class TestSQLiteWorkerAuditLogStore:
    """SQLite 实现的契约测试"""

    def test_append_log(self, sqlite_audit_log_adapter):
        """测试追加日志"""
        WorkerAuditLogAdapterContractTests(sqlite_audit_log_adapter).test_append_log()

    def test_list_logs_by_worker(self, sqlite_audit_log_adapter):
        """测试按 Worker ID 查询日志"""
        WorkerAuditLogAdapterContractTests(sqlite_audit_log_adapter).test_list_logs_by_worker()

    def test_list_logs_by_actions(self, sqlite_audit_log_adapter):
        """测试按动作类型过滤日志"""
        WorkerAuditLogAdapterContractTests(sqlite_audit_log_adapter).test_list_logs_by_actions()

    def test_list_logs_pagination(self, sqlite_audit_log_adapter):
        """测试日志分页"""
        WorkerAuditLogAdapterContractTests(sqlite_audit_log_adapter).test_list_logs_pagination()

    def test_get_latest_log(self, sqlite_audit_log_adapter):
        """测试获取最新日志"""
        WorkerAuditLogAdapterContractTests(sqlite_audit_log_adapter).test_get_latest_log()

    def test_get_latest_log_not_found(self, sqlite_audit_log_adapter):
        """测试获取不存在的最新日志"""
        WorkerAuditLogAdapterContractTests(sqlite_audit_log_adapter).test_get_latest_log_not_found()