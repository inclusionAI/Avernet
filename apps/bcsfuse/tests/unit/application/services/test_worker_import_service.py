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
            "state": {"availability": "protected", "trust_level": "trusted"},
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
            "state": {"availability": "protected", "trust_level": "trusted"},
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
            "state": {"availability": "protected", "trust_level": "trusted"},
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
            "state": {"availability": "protected", "trust_level": "trusted"},
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
            "state": {"availability": "protected", "trust_level": "trusted"},
        }

        service.import_from_api(worker_data, actor="test_user")

        # 检查索引同步
        assert service._index_sync_adapter.has_event("worker_created")

    def test_import_from_api_removes_created_worker_when_a_side_effect_fails(self):
        """A failed registration remains retryable instead of leaving a duplicate."""

        class FailOnceAuditLogStore(InMemoryWorkerAuditLogStore):
            def __init__(self):
                super().__init__()
                self._should_fail = True

            def append_log(self, audit_log):
                if self._should_fail:
                    self._should_fail = False
                    raise RuntimeError("audit unavailable")
                super().append_log(audit_log)

        registry_store = InMemoryWorkerRegistryStore()
        service = WorkerImportService(
            registry_store=registry_store,
            runtime_state_store=InMemoryWorkerRuntimeStateStore(),
            profile_binding_store=InMemoryWorkerProfileBindingStore(),
            audit_log_adapter=FailOnceAuditLogStore(),
            index_sync_adapter=InMemoryWorkerIndexSyncAdapter(),
        )
        worker_data = {
            "id": "wrk_retryable_registration",
            "type": "bot",
            "identity": {"name": "Retryable Bot", "handle": "@retryable-bot"},
            "responsibilities": ["testing"],
            "capabilities": [{"name": "test", "level": "expert"}],
            "state": {"availability": "protected", "trust_level": "unverified"},
            "active_profile_key": "wrk_retryable_registration:default",
        }

        with pytest.raises(RuntimeError, match="audit unavailable"):
            service.import_from_api(dict(worker_data), actor="test_user")

        assert registry_store.get_by_id("wrk_retryable_registration") is None

        created = service.import_from_api(dict(worker_data), actor="test_user")

        assert created.id == "wrk_retryable_registration"

    def test_import_from_api_compensates_partially_written_index(self):
        """An index failure removes both the durable worker and partial index data."""

        class FailOnceIndexSyncAdapter(InMemoryWorkerIndexSyncAdapter):
            def __init__(self):
                super().__init__()
                self._should_fail = True

            def on_worker_created(self, worker):
                super().on_worker_created(worker)
                if self._should_fail:
                    self._should_fail = False
                    raise RuntimeError("index unavailable")

        registry_store = InMemoryWorkerRegistryStore()
        index_sync = FailOnceIndexSyncAdapter()
        service = WorkerImportService(
            registry_store=registry_store,
            runtime_state_store=InMemoryWorkerRuntimeStateStore(),
            profile_binding_store=InMemoryWorkerProfileBindingStore(),
            audit_log_adapter=InMemoryWorkerAuditLogStore(),
            index_sync_adapter=index_sync,
        )
        worker_data = {
            "id": "wrk_partial_index",
            "type": "bot",
            "identity": {"name": "Indexed Bot", "handle": "@indexed-bot"},
            "responsibilities": ["testing"],
            "capabilities": [{"name": "test", "level": "expert"}],
            "state": {"availability": "protected", "trust_level": "unverified"},
        }

        with pytest.raises(RuntimeError, match="index unavailable"):
            service.import_from_api(dict(worker_data), actor="test_user")

        assert registry_store.get_by_id("wrk_partial_index") is None
        assert index_sync.get_calls_by_event("worker_deleted") == [
            {"event": "worker_deleted", "worker_id": "wrk_partial_index"}
        ]
