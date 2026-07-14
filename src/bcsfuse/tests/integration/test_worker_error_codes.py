"""
Integration Tests for Worker Error Codes

验证 Worker API 返回标准错误码 - AC-3, AC-5, AC-6
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


class TestWorkerErrorCodes:
    """Worker API 错误码测试"""

    def test_worker_not_found_returns_error_code(self, client):
        """
        AC-6: Worker 查询链路错误码覆盖

        Worker 查询 404 响应应包含 error_code: BCSFUSE-DOM-WORKER-NOT-FOUND
        """
        response = client.get("/v1/workers/non_existent_worker_id")

        assert response.status_code == 404
        data = response.json()

        # 验证响应包含 error_code 字段
        assert "error_code" in data
        assert data["error_code"] == "BCSFUSE-DOM-WORKER-NOT-FOUND"

        # 验证响应包含 message 字段
        assert "message" in data
        assert "non_existent_worker_id" in data["message"]

    def test_duplicate_worker_returns_error_code(self, client):
        """
        AC-5: Worker 注册链路错误码覆盖

        Worker 注册重复返回 error_code: BCSFUSE-DOM-DUPLICATE-WORKER
        """
        # 第一次创建
        response = client.post("/v1/workers", json={
            "id": "wrk_duplicate_test",
            "name": "Test Bot",
            "type": "bot",
            "availability": "public",  # 使用有效的枚举值
        })
        assert response.status_code == 201

        # 第二次创建相同 ID
        response = client.post("/v1/workers", json={
            "id": "wrk_duplicate_test",
            "name": "Test Bot 2",
            "availability": "public",  # 使用有效的枚举值
        })

        assert response.status_code == 409
        data = response.json()

        # 验证响应包含 error_code 字段
        assert "error_code" in data
        assert data["error_code"] == "BCSFUSE-DOM-DUPLICATE-WORKER"

        # 验证响应包含 message 字段
        assert "message" in data
        assert "wrk_duplicate_test" in data["message"]

    def test_set_online_worker_not_found_returns_error_code(self, client):
        """设置不存在的 Worker 为在线返回正确错误码"""
        response = client.put("/v1/workers/non_existent_worker/online")

        assert response.status_code == 404
        data = response.json()

        assert "error_code" in data
        assert data["error_code"] == "BCSFUSE-DOM-WORKER-NOT-FOUND"

    def test_set_offline_worker_not_found_returns_error_code(self, client):
        """设置不存在的 Worker 为离线返回正确错误码"""
        response = client.put("/v1/workers/non_existent_worker/offline")

        assert response.status_code == 404
        data = response.json()

        assert "error_code" in data
        assert data["error_code"] == "BCSFUSE-DOM-WORKER-NOT-FOUND"

    def test_update_worker_not_found_returns_error_code(self, client):
        """更新不存在的 Worker 返回正确错误码"""
        response = client.patch("/v1/workers/non_existent_worker", json={
            "name": "Updated Name",
        })

        assert response.status_code == 404
        data = response.json()

        assert "error_code" in data
        assert data["error_code"] == "BCSFUSE-DOM-WORKER-NOT-FOUND"

    def test_delete_worker_not_found_returns_error_code(self, client):
        """删除不存在的 Worker 返回正确错误码"""
        response = client.delete("/v1/workers/non_existent_worker")

        assert response.status_code == 404
        data = response.json()

        assert "error_code" in data
        assert data["error_code"] == "BCSFUSE-DOM-WORKER-NOT-FOUND"

    def test_error_response_format(self, client):
        """
        AC-3: 接口层错误码透出

        验证错误响应格式符合规范
        """
        response = client.get("/v1/workers/non_existent_worker")

        assert response.status_code == 404
        data = response.json()

        # 验证响应格式
        assert "error_code" in data
        assert "message" in data
        assert isinstance(data["error_code"], str)
        assert isinstance(data["message"], str)

        # 验证 error_code 格式符合规范 BCSFUSE-{层级前缀}-{业务语义}
        assert data["error_code"].startswith("BCSFUSE-")
        parts = data["error_code"].split("-")
        assert len(parts) >= 3  # BCSFUSE-DOM-WORKER-NOT-FOUND