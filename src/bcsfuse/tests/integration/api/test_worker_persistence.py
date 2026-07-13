"""
Integration Tests for Worker Persistence

测试 Worker 数据持久化到 SQLite。

验证：
- Worker 创建后数据持久化
- 运行态切换后持久化
- 审计日志持久化
- 重启后数据不丢失
"""

import os
import tempfile
import pytest
from fastapi.testclient import TestClient

from src.interfaces.api.app import app
from src.interfaces.api.dependencies.worker_dependencies import (
    reset_stores,
    _registry_store,
    _runtime_state_store,
    _audit_log_store,
)
from src.infra.config.worker_registry_settings import WorkerRegistrySettings
from src.infra.adapters.sqlite_worker_registry_store import SQLiteWorkerRegistryStore
from src.infra.adapters.sqlite_worker_runtime_state_store import SQLiteWorkerRuntimeStateStore
from src.infra.adapters.sqlite_worker_audit_log_store import SQLiteWorkerAuditLogStore
from src.application.services.worker_import_service import WorkerImportService
from src.application.services.worker_runtime_state_service import WorkerRuntimeStateService
from src.infra.adapters.in_memory_worker_index_sync_adapter import InMemoryWorkerIndexSyncAdapter
from src.infra.adapters.sqlite_worker_profile_binding_store import SQLiteWorkerProfileBindingStore


@pytest.fixture
def temp_db_path():
    """创建临时数据库文件"""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    yield db_path
    # 清理临时文件
    if os.path.exists(db_path):
        os.unlink(db_path)


@pytest.fixture
def persistent_stores(temp_db_path):
    """创建持久化存储实例"""
    registry_store = SQLiteWorkerRegistryStore(temp_db_path)
    runtime_state_store = SQLiteWorkerRuntimeStateStore(temp_db_path)
    profile_binding_store = SQLiteWorkerProfileBindingStore(temp_db_path)
    audit_log_store = SQLiteWorkerAuditLogStore(temp_db_path)
    index_sync_adapter = InMemoryWorkerIndexSyncAdapter()

    yield {
        "registry_store": registry_store,
        "runtime_state_store": runtime_state_store,
        "profile_binding_store": profile_binding_store,
        "audit_log_store": audit_log_store,
        "index_sync_adapter": index_sync_adapter,
        "db_path": temp_db_path,
    }

    # 清理
    registry_store.close()
    runtime_state_store.close()
    profile_binding_store.close()
    audit_log_store.close()


class TestWorkerPersistence:
    """Worker 持久化测试"""

    def test_worker_survives_restart(self, persistent_stores):
        """测试 Worker 数据在重启后保留"""
        stores = persistent_stores

        # 创建 Import Service
        import_service = WorkerImportService(
            registry_store=stores["registry_store"],
            runtime_state_store=stores["runtime_state_store"],
            profile_binding_store=stores["profile_binding_store"],
            audit_log_adapter=stores["audit_log_store"],
            index_sync_adapter=stores["index_sync_adapter"],
        )

        # 创建 Worker
        import_service.import_from_api({
            "id": "wrk_persist_001",
            "type": "bot",
            "identity": {"name": "Persistent Bot", "handle": "@persistent-bot"},
            "responsibilities": ["testing"],
            "capabilities": [{"name": "testing", "level": "expert"}],
            "state": {"availability": "available", "trust_level": "trusted"},
        })

        # 关闭连接
        stores["registry_store"].close()
        stores["runtime_state_store"].close()
        stores["audit_log_store"].close()

        # 重新打开连接
        new_registry_store = SQLiteWorkerRegistryStore(stores["db_path"])
        stores["registry_store"] = new_registry_store

        # 验证 Worker 仍然存在
        worker = new_registry_store.get_by_id("wrk_persist_001")
        assert worker is not None
        assert worker.id == "wrk_persist_001"
        assert worker.identity.name == "Persistent Bot"
        # Pydantic use_enum_values=True 使枚举字段成为字符串
        assert worker.source_type == "api"
        assert worker.lifecycle_state == "active"

    def test_runtime_state_survives_restart(self, persistent_stores):
        """测试运行态在重启后保留"""
        stores = persistent_stores

        # 创建 Services
        import_service = WorkerImportService(
            registry_store=stores["registry_store"],
            runtime_state_store=stores["runtime_state_store"],
            profile_binding_store=stores["profile_binding_store"],
            audit_log_adapter=stores["audit_log_store"],
            index_sync_adapter=stores["index_sync_adapter"],
        )
        runtime_service = WorkerRuntimeStateService(
            registry_store=stores["registry_store"],
            runtime_state_store=stores["runtime_state_store"],
            audit_log_adapter=stores["audit_log_store"],
            index_sync_adapter=stores["index_sync_adapter"],
        )

        # 创建 Worker
        import_service.import_from_api({
            "id": "wrk_runtime_persist_001",
            "type": "bot",
            "identity": {"name": "Runtime Persist Bot", "handle": "@runtime-persist"},
            "responsibilities": ["testing"],
            "capabilities": [{"name": "testing", "level": "expert"}],
            "state": {"availability": "available", "trust_level": "trusted"},
        })

        # 设置为在线
        runtime_service.set_online("wrk_runtime_persist_001")

        # 关闭连接
        stores["registry_store"].close()
        stores["runtime_state_store"].close()
        stores["audit_log_store"].close()

        # 重新打开连接
        new_registry_store = SQLiteWorkerRegistryStore(stores["db_path"])
        new_runtime_store = SQLiteWorkerRuntimeStateStore(stores["db_path"])
        stores["registry_store"] = new_registry_store
        stores["runtime_state_store"] = new_runtime_store

        # 验证运行态
        from src.domain.models.worker_runtime_state import WorkerRuntimeState
        runtime_state = new_runtime_store.get_runtime_state("wrk_runtime_persist_001")
        assert runtime_state == WorkerRuntimeState.ONLINE

    def test_audit_log_survives_restart(self, persistent_stores):
        """测试审计日志在重启后保留"""
        stores = persistent_stores

        # 创建 Services
        import_service = WorkerImportService(
            registry_store=stores["registry_store"],
            runtime_state_store=stores["runtime_state_store"],
            profile_binding_store=stores["profile_binding_store"],
            audit_log_adapter=stores["audit_log_store"],
            index_sync_adapter=stores["index_sync_adapter"],
        )

        # 创建 Worker（会写审计日志）
        import_service.import_from_api({
            "id": "wrk_audit_persist_001",
            "type": "bot",
            "identity": {"name": "Audit Persist Bot", "handle": "@audit-persist"},
            "responsibilities": ["testing"],
            "capabilities": [{"name": "testing", "level": "expert"}],
            "state": {"availability": "available", "trust_level": "trusted"},
        })

        # 关闭连接
        stores["audit_log_store"].close()

        # 重新打开连接
        new_audit_store = SQLiteWorkerAuditLogStore(stores["db_path"])
        stores["audit_log_store"] = new_audit_store

        # 验证审计日志
        logs = new_audit_store.list_logs(worker_id="wrk_audit_persist_001")
        assert len(logs) >= 1
        assert logs[0].worker_id == "wrk_audit_persist_001"
        assert logs[0].action.value == "created"

    def test_create_worker_persists_to_sqlite(self, persistent_stores):
        """测试创建 Worker 持久化到 SQLite"""
        stores = persistent_stores

        # 创建 Import Service
        import_service = WorkerImportService(
            registry_store=stores["registry_store"],
            runtime_state_store=stores["runtime_state_store"],
            profile_binding_store=stores["profile_binding_store"],
            audit_log_adapter=stores["audit_log_store"],
            index_sync_adapter=stores["index_sync_adapter"],
        )

        # 创建 Worker
        worker = import_service.import_from_api({
            "id": "wrk_sqlite_001",
            "type": "bot",
            "identity": {"name": "SQLite Bot", "handle": "@sqlite-bot"},
            "responsibilities": ["persistence"],
            "domains": ["testing"],
            "capabilities": [{"name": "persistence", "level": "expert"}],
            "state": {"availability": "available", "trust_level": "trusted"},
        })

        # 验证返回的 Worker
        assert worker.id == "wrk_sqlite_001"
        assert worker.source_type == "api"
        assert worker.version == 1

        # 直接从 store 验证
        found = stores["registry_store"].get_by_id("wrk_sqlite_001")
        assert found is not None
        assert found.id == "wrk_sqlite_001"
        assert found.domains == ["testing"]

        # 验证运行态已初始化
        from src.domain.models.worker_runtime_state import WorkerRuntimeState
        runtime_state = stores["runtime_state_store"].get_runtime_state("wrk_sqlite_001")
        assert runtime_state == WorkerRuntimeState.OFFLINE

        # 验证审计日志已写入
        logs = stores["audit_log_store"].list_logs(worker_id="wrk_sqlite_001")
        assert len(logs) >= 1


class TestOnlineOfflinePersistence:
    """Online/Offline 切换持久化测试"""

    def test_online_offline_persists(self, persistent_stores):
        """测试 online/offline 切换后持久化"""
        stores = persistent_stores

        # 创建 Services
        import_service = WorkerImportService(
            registry_store=stores["registry_store"],
            runtime_state_store=stores["runtime_state_store"],
            profile_binding_store=stores["profile_binding_store"],
            audit_log_adapter=stores["audit_log_store"],
            index_sync_adapter=stores["index_sync_adapter"],
        )
        runtime_service = WorkerRuntimeStateService(
            registry_store=stores["registry_store"],
            runtime_state_store=stores["runtime_state_store"],
            audit_log_adapter=stores["audit_log_store"],
            index_sync_adapter=stores["index_sync_adapter"],
        )

        # 创建 Worker
        import_service.import_from_api({
            "id": "wrk_switch_001",
            "type": "bot",
            "identity": {"name": "Switch Bot", "handle": "@switch-bot"},
            "responsibilities": ["testing"],
            "capabilities": [{"name": "testing", "level": "expert"}],
            "state": {"availability": "available", "trust_level": "trusted"},
        })

        # 设为在线
        runtime_service.set_online("wrk_switch_001")
        from src.domain.models.worker_runtime_state import WorkerRuntimeState
        assert stores["runtime_state_store"].get_runtime_state("wrk_switch_001") == WorkerRuntimeState.ONLINE

        # 设为离线
        runtime_service.set_offline("wrk_switch_001")
        assert stores["runtime_state_store"].get_runtime_state("wrk_switch_001") == WorkerRuntimeState.OFFLINE

        # 验证审计日志
        logs = stores["audit_log_store"].list_logs(worker_id="wrk_switch_001")
        # created + runtime_state_changed (online) + runtime_state_changed (offline)
        assert len(logs) >= 2