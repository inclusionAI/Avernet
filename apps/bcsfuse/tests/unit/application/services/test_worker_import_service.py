"""
Tests for Worker Import Service

Stage 1 Service Tests
"""

import pytest

from src.application.services.worker_import_service import WorkerImportService
from src.infra.adapters.in_memory_worker_registry_store import InMemoryWorkerRegistryStore
from src.infra.adapters.in_memory_worker_runtime_state_store import InMemoryWorkerRuntimeStateStore
from src.infra.adapters.in_memory_worker_profile_binding_store import InMemoryWorkerProfileBindingStore
from src.infra.adapters.in_memory_worker_audit_log_store import InMemoryWorkerAuditLogStore
from src.infra.adapters.in_memory_worker_index_sync_adapter import InMemoryWorkerIndexSyncAdapter
from src.domain.models.worker_lifecycle_state import WorkerLifecycleState
from src.domain.models.worker_runtime_state import WorkerRuntimeState
from src.domain.models.worker_source_info import WorkerSourceType
from src.domain.exceptions import DuplicateWorkerException


class TestWorkerImportService:
    """WorkerImportService 测试"""

    @pytest.fixture
    def service(self):
        """创建服务实例"""
        return WorkerImportService(
            registry_store=InMemoryWorkerRegistryStore(),
            runtime_state_store=InMemoryWorkerRuntimeStateStore(),
            profile_binding_store=InMemoryWorkerProfileBindingStore(),
            audit_log_adapter=InMemoryWorkerAuditLogStore(),
            index_sync_adapter=InMemoryWorkerIndexSyncAdapter(),
        )

    def test_import_from_api(self, service):
        """测试 API 注册"""
        worker_data = {
            "id": "wrk_api_001",
            "type": "bot",
            "identity": {"name": "API Bot", "handle": "@api-bot"},
            "responsibilities": ["testing"],
            "capabilities": [{"name": "test", "level": "expert"}],
            "state": {"availability": "available", "trust_level": "trusted"},
        }

        worker = service.import_from_api(worker_data, actor="test_user")

        assert worker.id == "wrk_api_001"
        assert worker.source_type == WorkerSourceType.API
        assert worker.lifecycle_state == WorkerLifecycleState.ACTIVE

    def test_import_from_api_duplicate(self, service):
        """测试 API 注册重复"""
        worker_data = {
            "id": "wrk_api_001",
            "type": "bot",
            "identity": {"name": "API Bot", "handle": "@api-bot"},
            "responsibilities": ["testing"],
            "capabilities": [{"name": "test", "level": "expert"}],
            "state": {"availability": "available", "trust_level": "trusted"},
        }

        service.import_from_api(worker_data, actor="test_user")

        with pytest.raises(DuplicateWorkerException):
            service.import_from_api(worker_data, actor="test_user")

    def test_import_from_api_sets_offline(self, service):
        """测试 API 注册默认为 offline"""
        worker_data = {
            "id": "wrk_api_002",
            "type": "bot",
            "identity": {"name": "API Bot", "handle": "@api-bot"},
            "responsibilities": ["testing"],
            "capabilities": [{"name": "test", "level": "expert"}],
            "state": {"availability": "available", "trust_level": "trusted"},
        }

        worker = service.import_from_api(worker_data, actor="test_user")

        # 检查 runtime state
        runtime_state = service._runtime_state_store.get_runtime_state(worker.id)
        assert runtime_state == WorkerRuntimeState.OFFLINE

    def test_import_from_api_creates_audit_log(self, service):
        """测试 API 注册创建审计日志"""
        worker_data = {
            "id": "wrk_api_003",
            "type": "bot",
            "identity": {"name": "API Bot", "handle": "@api-bot"},
            "responsibilities": ["testing"],
            "capabilities": [{"name": "test", "level": "expert"}],
            "state": {"availability": "available", "trust_level": "trusted"},
        }

        service.import_from_api(worker_data, actor="test_user")

        # 检查审计日志
        logs = service._audit_log_adapter.list_logs(worker_id="wrk_api_003")
        assert len(logs) == 1
        assert logs[0].action.value == "created"

    def test_import_from_api_triggers_index_sync(self, service):
        """测试 API 注册触发索引同步"""
        worker_data = {
            "id": "wrk_api_004",
            "type": "bot",
            "identity": {"name": "API Bot", "handle": "@api-bot"},
            "responsibilities": ["testing"],
            "capabilities": [{"name": "test", "level": "expert"}],
            "state": {"availability": "available", "trust_level": "trusted"},
        }

        service.import_from_api(worker_data, actor="test_user")

        # 检查索引同步
        assert service._index_sync_adapter.has_event("worker_created")