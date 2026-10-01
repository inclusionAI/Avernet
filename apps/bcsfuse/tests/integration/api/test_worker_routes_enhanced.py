"""
Integration Tests for Worker Routes Enhanced

测试 Stage 1 Phase 3 增强的 Worker API 端点。

测试覆盖：
- POST /workers - 创建 Worker
- GET /workers/{id} - 获取 Worker
- GET /workers - 列表查询
- PUT /workers/{id}/online - 设为在线
- PUT /workers/{id}/offline - 设为离线
"""

import pytest
from fastapi.testclient import TestClient

from src.interfaces.api.app import app
from src.interfaces.api.dependencies.worker_dependencies import (
    reset_stores,
    use_in_memory_stores,
)


@pytest.fixture(autouse=True)
def setup_in_memory_stores():
    """每个测试前重置为内存存储"""
    use_in_memory_stores()
    yield
    reset_stores()


@pytest.fixture
def client():
    """创建测试客户端"""
    return TestClient(app)


class TestCreateWorker:
    """创建 Worker 测试"""

    def test_create_worker_success(self, client):
        """测试成功创建 Worker"""
        response = client.post("/v1/workers", json={
            "id": "wrk_test_001",
            "name": "Test Bot",
            "type": "bot",
            "responsibilities": ["testing"],
            "domains": ["ai", "ml"],
            "capabilities": [{"name": "test", "level": "expert"}],
        })

        assert response.status_code == 201
        data = response.json()
        assert data["id"] == "wrk_test_001"
        assert data["name"] == "Test Bot"
        assert data["type"] == "bot"
        assert data["lifecycle_state"] == "active"
        assert data["runtime_state"] == "offline"
        assert data["source_type"] == "api"
        assert data["version"] == 1

    def test_create_worker_duplicate_rejected(self, client):
        """测试重复创建 Worker 被拒绝"""
        # 第一次创建
        client.post("/v1/workers", json={
            "id": "wrk_dup_001",
            "name": "Duplicate Bot",
        })

        # 第二次创建相同 ID
        response = client.post("/v1/workers", json={
            "id": "wrk_dup_001",
            "name": "Duplicate Bot 2",
        })

        assert response.status_code == 409
        # 使用新的响应格式：error_code 字段
        data = response.json()
        assert "error_code" in data
        assert "DUPLICATE-WORKER" in data["error_code"]

    def test_create_worker_returns_lifecycle_and_runtime(self, client):
        """测试创建 Worker 返回 lifecycle 和 runtime 字段"""
        response = client.post("/v1/workers", json={
            "id": "wrk_fields_001",
            "name": "Fields Bot",
        })

        assert response.status_code == 201
        data = response.json()
        assert "lifecycle_state" in data
        assert "runtime_state" in data
        assert data["lifecycle_state"] == "active"
        assert data["runtime_state"] == "offline"


class TestGetWorker:
    """获取 Worker 测试"""

    def test_get_worker_success(self, client):
        """测试成功获取 Worker"""
        # 先创建
        client.post("/v1/workers", json={
            "id": "wrk_get_001",
            "name": "Get Bot",
        })

        # 再获取
        response = client.get("/v1/workers/wrk_get_001")

        assert response.status_code == 200
        data = response.json()
        assert data["id"] == "wrk_get_001"
        assert data["name"] == "Get Bot"

    def test_get_worker_not_found(self, client):
        """测试获取不存在的 Worker"""
        response = client.get("/v1/workers/wrk_not_exist")

        assert response.status_code == 404
        # 使用新的响应格式：error_code 字段
        data = response.json()
        assert "error_code" in data
        assert "WORKER-NOT-FOUND" in data["error_code"]


class TestListWorkers:
    """列表查询 Worker 测试"""

    def test_list_workers(self, client):
        """测试列出所有 Worker"""
        # 创建多个 Worker
        for i in range(3):
            client.post("/v1/workers", json={
                "id": f"wrk_list_{i:03d}",
                "name": f"List Bot {i}",
            })

        response = client.get("/v1/workers")

        assert response.status_code == 200
        data = response.json()
        assert len(data["items"]) >= 3

    def test_list_workers_filter_by_lifecycle(self, client):
        """测试按生命周期状态过滤"""
        # 创建 Worker
        client.post("/v1/workers", json={
            "id": "wrk_list_active",
            "name": "Active Bot",
        })

        response = client.get("/v1/workers?lifecycle_state=active")

        assert response.status_code == 200
        data = response.json()
        for item in data["items"]:
            assert item["lifecycle_state"] == "active"

    def test_list_workers_filter_by_runtime_state(self, client):
        """测试按运行态过滤"""
        # 创建并设置为在线
        client.post("/v1/workers", json={
            "id": "wrk_list_online",
            "name": "Online Bot",
        })
        client.put("/v1/workers/wrk_list_online/online")

        # 创建但不设置在线
        client.post("/v1/workers", json={
            "id": "wrk_list_offline",
            "name": "Offline Bot",
        })

        # 过滤 online
        response = client.get("/v1/workers?runtime_state=online")
        assert response.status_code == 200
        data = response.json()
        for item in data["items"]:
            assert item["runtime_state"] == "online"

    def test_list_workers_filter_by_source_type(self, client):
        """测试按来源类型过滤"""
        client.post("/v1/workers", json={
            "id": "wrk_list_api",
            "name": "API Bot",
        })

        response = client.get("/v1/workers?source_type=api")

        assert response.status_code == 200
        data = response.json()
        for item in data["items"]:
            assert item["source_type"] == "api"


class TestSetWorkerOnline:
    """设置 Worker 在线测试"""

    def test_set_worker_online_success(self, client):
        """测试成功设置 Worker 在线"""
        # 创建 Worker
        client.post("/v1/workers", json={
            "id": "wrk_online_001",
            "name": "Online Bot",
        })

        # 设置为在线
        response = client.put("/v1/workers/wrk_online_001/online")

        assert response.status_code == 200
        data = response.json()
        assert data["worker_id"] == "wrk_online_001"
        assert data["runtime_state"] == "online"

    def test_set_disabled_worker_online_fails(self, client):
        """测试 disabled worker 不能设为 online"""
        # 创建 Worker 后直接更新 lifecycle_state
        client.post("/v1/workers", json={
            "id": "wrk_disabled_001",
            "name": "Disabled Bot",
        })

        # 手动设置为 disabled（通过 patch）
        from src.interfaces.api.dependencies.worker_dependencies import get_registry_store
        store = get_registry_store()
        worker = store.get_by_id("wrk_disabled_001")
        worker.lifecycle_state = "disabled"  # 使用字符串
        from src.domain.models.worker_lifecycle_state import WorkerLifecycleState
        worker.lifecycle_state = WorkerLifecycleState.DISABLED
        store.update(worker)

        # 尝试设置为在线
        response = client.put("/v1/workers/wrk_disabled_001/online")

        assert response.status_code == 400
        assert "INVALID_STATE_TRANSITION" in response.json()["detail"]["code"]

    def test_set_inactive_worker_online_fails(self, client):
        """测试 inactive worker 不能设为 online"""
        # 创建 Worker
        client.post("/v1/workers", json={
            "id": "wrk_inactive_001",
            "name": "Inactive Bot",
        })

        # 手动设置为 inactive
        from src.interfaces.api.dependencies.worker_dependencies import get_registry_store
        store = get_registry_store()
        worker = store.get_by_id("wrk_inactive_001")
        from src.domain.models.worker_lifecycle_state import WorkerLifecycleState
        worker.lifecycle_state = WorkerLifecycleState.INACTIVE
        store.update(worker)

        # 尝试设置为在线
        response = client.put("/v1/workers/wrk_inactive_001/online")

        assert response.status_code == 400


class TestSetWorkerOffline:
    """设置 Worker 离线测试"""

    def test_set_worker_offline_success(self, client):
        """测试成功设置 Worker 离线"""
        # 创建 Worker
        client.post("/v1/workers", json={
            "id": "wrk_offline_001",
            "name": "Offline Bot",
        })

        # 先设置为在线
        client.put("/v1/workers/wrk_offline_001/online")

        # 再设置为离线
        response = client.put("/v1/workers/wrk_offline_001/offline")

        assert response.status_code == 200
        data = response.json()
        assert data["runtime_state"] == "offline"

    def test_set_worker_offline_from_offline_is_idempotent(self, client):
        """测试从离线设为离线是幂等的"""
        # 创建 Worker（默认 offline）
        client.post("/v1/workers", json={
            "id": "wrk_offline_idem",
            "name": "Offline Idempotent Bot",
        })

        # 设置为离线
        response = client.put("/v1/workers/wrk_offline_idem/offline")

        assert response.status_code == 200
        assert response.json()["runtime_state"] == "offline"