"""
Worker API Contract Tests

验证 Worker API 端点符合 OpenAPI 契约。

Stage 1: 验证 API 端点存在并返回正确格式。

注意：Stage 1 API 使用扁平化请求格式（name/handle 在顶层），而非嵌套的 identity 结构。
"""

import pytest
from fastapi.testclient import TestClient


AUTH_HEADERS = {"Authorization": "Bearer test-token"}


@pytest.fixture
def composed_test_client(monkeypatch):
    """Build the public composition root used by A2/O3 contract parity."""
    monkeypatch.setenv("BCSFUSE_AUTH_TOKEN", "test-token")
    monkeypatch.setenv("BCSFUSE_PROVIDER_MODE", "runtime")
    monkeypatch.setenv("ENABLE_PROFILE_EMBEDDING_INDEX", "false")
    monkeypatch.delenv("BCSFUSE_EXPOSE_ADMIN", raising=False)

    from src.bootstrap.opensource_app import create_opensource_app

    app = create_opensource_app(mode="test")
    with TestClient(app) as client:
        yield client, app


def _worker_payload(worker_id: str) -> dict:
    return {
        "id": worker_id,
        "type": "bot",
        "name": "Contract Bot",
        "handle": f"@{worker_id}",
        "responsibilities": ["contract validation"],
        "capabilities": [{"name": "testing", "level": "expert"}],
        "availability": "public",
        "trust_level": "trusted",
    }


class CanonicalOnlyProfileStore:
    """Profile provider exposing exactly the published typed port."""

    def __init__(self):
        from src.infra.public.stores.in_memory_worker_profile_content_store import (
            InMemoryWorkerProfileContentStore,
        )

        self._delegate = InMemoryWorkerProfileContentStore()

    def save(self, content):
        return self._delegate.save(content)

    def get(self, worker_id, profile_id):
        return self._delegate.get(worker_id, profile_id)

    def list_by_worker(self, worker_id):
        return self._delegate.list_by_worker(worker_id)

    def delete(self, worker_id, profile_id):
        return self._delegate.delete(worker_id, profile_id)

    def activate(self, worker_id, profile_id):
        return self._delegate.activate(worker_id, profile_id)

    def get_active(self, worker_id):
        return self._delegate.get_active(worker_id)

    def exists(self, worker_id, profile_id):
        return self._delegate.exists(worker_id, profile_id)

    def count(self, worker_id=None):
        return self._delegate.count(worker_id)

    def get_all_active(self):
        return self._delegate.get_all_active()


def test_composed_app_matches_worker_profile_lifecycle_contract(composed_test_client):
    """Freeze the Worker/Profile request corpus shared with the internal app."""
    client, app = composed_test_client
    worker_id = "wrk_composed_contract"
    profile_id = "release"

    created = client.post(
        "/v1/workers",
        headers=AUTH_HEADERS,
        json=_worker_payload(worker_id),
    )
    assert created.status_code == 201, created.text
    assert {
        "id": created.json()["id"],
        "lifecycle_state": created.json()["lifecycle_state"],
        "runtime_state": created.json()["runtime_state"],
    } == {
        "id": worker_id,
        "lifecycle_state": "active",
        "runtime_state": "offline",
    }

    fetched = client.get(f"/v1/workers/{worker_id}", headers=AUTH_HEADERS)
    assert fetched.status_code == 200, fetched.text
    assert fetched.json()["id"] == worker_id

    online = client.put(
        f"/v1/workers/{worker_id}/online",
        headers=AUTH_HEADERS,
    )
    assert online.status_code == 200, online.text
    assert online.json()["runtime_state"] == "online"
    assert online.json()["lifecycle_state"] == "active"

    profile = client.put(
        f"/v1/workers/{worker_id}/profiles/{profile_id}",
        headers=AUTH_HEADERS,
        json={
            "display_name": "Release Validation",
            "soul_md": "# Release Validation",
        },
    )
    assert profile.status_code == 200, profile.text
    assert profile.json()["profile_id"] == profile_id

    activated = client.put(
        f"/v1/workers/{worker_id}/profiles/{profile_id}/activate",
        headers=AUTH_HEADERS,
    )
    assert activated.status_code == 200, activated.text
    assert activated.json()["is_active"] is True
    assert activated.json()["binding_updated"] is True
    assert activated.json()["worker_updated"] is True
    activated_worker = app.state.context.registry.get(
        "worker_registry_store"
    ).get_by_id(worker_id)
    assert activated_worker.active_profile_key == f"{worker_id}:{profile_id}"

    read_profile = client.get(
        f"/v1/workers/{worker_id}/profiles/{profile_id}",
        headers=AUTH_HEADERS,
    )
    assert read_profile.status_code == 200, read_profile.text
    assert read_profile.json()["display_name"] == "Release Validation"
    assert read_profile.json()["is_active"] is True

    offline = client.put(
        f"/v1/workers/{worker_id}/offline",
        headers=AUTH_HEADERS,
    )
    assert offline.status_code == 200, offline.text
    assert offline.json()["runtime_state"] == "offline"

    registry = app.state.context.registry
    assert registry.get("worker_registry_store").get_by_id(worker_id) is not None
    runtime_state = registry.get("worker_runtime_state_store").get_runtime_state(
        worker_id
    )
    assert getattr(runtime_state, "value", runtime_state) == "offline"
    binding = registry.get("worker_profile_binding_store").get_active_binding(
        worker_id
    )
    assert binding is not None
    assert binding.profile_key == f"{worker_id}:{profile_id}"

    deleted = client.delete(f"/v1/workers/{worker_id}", headers=AUTH_HEADERS)
    assert deleted.status_code == 200, deleted.text
    assert deleted.json() == {
        "success": True,
        "worker_id": worker_id,
        "deleted": True,
    }
    assert registry.get("worker_registry_store").get_by_id(worker_id) is None

    deleted_again = client.delete(
        f"/v1/workers/{worker_id}",
        headers=AUTH_HEADERS,
    )
    assert deleted_again.status_code == 404, deleted_again.text
    assert deleted_again.json()["detail"]["code"] == "WORKER_NOT_FOUND"


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", "/v1/workers/wrk_missing_contract"),
        ("put", "/v1/workers/wrk_missing_contract/online"),
        ("put", "/v1/workers/wrk_missing_contract/offline"),
        ("put", "/api/v1/workers/wrk_missing_contract/online"),
        ("put", "/api/v1/workers/wrk_missing_contract/offline"),
        ("delete", "/v1/workers/wrk_missing_contract"),
    ],
)
def test_composed_app_returns_stable_missing_worker_error(
    composed_test_client,
    method,
    path,
):
    client, _app = composed_test_client

    response = getattr(client, method)(path, headers=AUTH_HEADERS)

    assert response.status_code == 404, response.text
    assert response.json()["detail"]["code"] == "WORKER_NOT_FOUND"


def test_composed_profile_routes_accept_canonical_only_provider(monkeypatch):
    monkeypatch.setenv("BCSFUSE_AUTH_TOKEN", "test-token")
    monkeypatch.setenv("BCSFUSE_PROVIDER_MODE", "runtime")
    monkeypatch.setenv("ENABLE_PROFILE_EMBEDDING_INDEX", "false")
    from src.bootstrap.app_factory import create_bcsfuse_app
    from src.bootstrap.application_context import build_application_context

    context = build_application_context(mode="test")
    canonical_store = CanonicalOnlyProfileStore()
    context.registry.register("worker_profile_content_store", canonical_store)
    app = create_bcsfuse_app(context)
    assert app.state.context.registry.get("worker_profile_content_store") is not canonical_store

    worker_id = "wrk_canonical_profile_provider"
    profile_id = "default"
    with TestClient(app) as client:
        assert client.post(
            "/v1/workers",
            headers=AUTH_HEADERS,
            json=_worker_payload(worker_id),
        ).status_code == 201

        upserted = client.put(
            f"/v1/workers/{worker_id}/profiles/{profile_id}",
            headers=AUTH_HEADERS,
            json={"display_name": "Canonical Provider", "soul_md": "# Canonical"},
        )
        activated = client.put(
            f"/v1/workers/{worker_id}/profiles/{profile_id}/activate",
            headers=AUTH_HEADERS,
        )
        updated = client.put(
            f"/v1/workers/{worker_id}/profiles/{profile_id}",
            headers=AUTH_HEADERS,
            json={"display_name": "Canonical Updated", "soul_md": "# Updated"},
        )
        fetched = client.get(
            f"/v1/workers/{worker_id}/profiles/{profile_id}",
            headers=AUTH_HEADERS,
        )

    assert upserted.status_code == 200, upserted.text
    assert activated.status_code == 200, activated.text
    assert activated.json()["binding_updated"] is True
    assert updated.status_code == 200, updated.text
    assert fetched.status_code == 200, fetched.text
    assert fetched.json()["display_name"] == "Canonical Updated"
    assert fetched.json()["is_active"] is True


def test_worker_delete_failure_preserves_durable_worker_and_profile(
    composed_test_client,
    monkeypatch,
):
    client, app = composed_test_client
    worker_id = "wrk_delete_failure_contract"
    profile_id = "default"
    assert client.post(
        "/v1/workers",
        headers=AUTH_HEADERS,
        json=_worker_payload(worker_id),
    ).status_code == 201
    assert client.put(
        f"/v1/workers/{worker_id}/profiles/{profile_id}",
        headers=AUTH_HEADERS,
        json={"display_name": "Durable Profile", "soul_md": "# Durable"},
    ).status_code == 200

    registry = app.state.context.registry
    worker_store = registry.get("worker_registry_store")

    def fail_delete(_worker_id):
        raise RuntimeError("durable worker delete failed")

    monkeypatch.setattr(worker_store, "delete", fail_delete)
    response = client.delete(f"/v1/workers/{worker_id}", headers=AUTH_HEADERS)

    assert response.status_code == 500
    assert response.json()["detail"]["code"] == "DELETE_WORKER_ERROR"
    assert worker_store.get_by_id(worker_id) is not None
    profile_store = registry.get("worker_profile_content_store")
    assert profile_store.get(worker_id, profile_id) is not None


def test_profile_activation_propagates_binding_write_failure(
    composed_test_client,
    monkeypatch,
):
    client, app = composed_test_client
    worker_id = "wrk_binding_failure_contract"
    profile_id = "default"
    assert client.post(
        "/v1/workers",
        headers=AUTH_HEADERS,
        json=_worker_payload(worker_id),
    ).status_code == 201
    assert client.put(
        f"/v1/workers/{worker_id}/profiles/{profile_id}",
        headers=AUTH_HEADERS,
        json={"display_name": "Binding Failure", "soul_md": "# Binding"},
    ).status_code == 200

    registry = app.state.context.registry
    binding_store = registry.get("worker_profile_binding_store")

    def fail_bind(**_kwargs):
        raise RuntimeError("binding persistence failed")

    monkeypatch.setattr(binding_store, "bind_profile", fail_bind)
    response = client.put(
        f"/v1/workers/{worker_id}/profiles/{profile_id}/activate",
        headers=AUTH_HEADERS,
    )

    assert response.status_code == 500
    assert response.json()["detail"]["code"] == "ACTIVATE_PROFILE_ERROR"
    assert registry.get("worker_profile_content_store").get_active(worker_id) is None


def test_profile_activation_failure_compensates_new_binding(
    composed_test_client,
    monkeypatch,
):
    client, app = composed_test_client
    worker_id = "wrk_activation_failure_contract"
    profile_id = "default"
    assert client.post(
        "/v1/workers",
        headers=AUTH_HEADERS,
        json=_worker_payload(worker_id),
    ).status_code == 201
    assert client.put(
        f"/v1/workers/{worker_id}/profiles/{profile_id}",
        headers=AUTH_HEADERS,
        json={"display_name": "Activation Failure", "soul_md": "# Activation"},
    ).status_code == 200

    registry = app.state.context.registry
    profile_store = registry.get("worker_profile_content_store")
    monkeypatch.setattr(profile_store, "activate", lambda *_args: None)

    response = client.put(
        f"/v1/workers/{worker_id}/profiles/{profile_id}/activate",
        headers=AUTH_HEADERS,
    )

    assert response.status_code == 500
    assert response.json()["detail"]["code"] == "ACTIVATE_PROFILE_ERROR"
    assert (
        registry.get("worker_profile_binding_store").get_active_binding(worker_id)
        is None
    )


def test_runtime_registry_write_failure_rolls_back_and_returns_error(
    composed_test_client,
    monkeypatch,
):
    client, app = composed_test_client
    worker_id = "wrk_runtime_failure_contract"
    assert client.post(
        "/v1/workers",
        headers=AUTH_HEADERS,
        json=_worker_payload(worker_id),
    ).status_code == 201

    registry = app.state.context.registry
    worker_store = registry.get("worker_registry_store")

    def fail_update(_worker):
        raise RuntimeError("worker registry update failed")

    monkeypatch.setattr(worker_store, "update", fail_update)
    response = client.put(
        f"/v1/workers/{worker_id}/online",
        headers=AUTH_HEADERS,
    )

    assert response.status_code == 500
    assert response.json()["detail"]["code"] == "RUNTIME_STATE_UPDATE_ERROR"
    runtime_state = registry.get("worker_runtime_state_store").get_runtime_state(
        worker_id
    )
    assert getattr(runtime_state, "value", runtime_state) == "offline"
    assert worker_store.get_by_id(worker_id).state.runtime_state.value == "offline"


class TestWorkerAPISetup:
    """Worker API 设置测试"""

    def test_app_importable(self):
        """验证 FastAPI app 可导入"""
        from src.interfaces.api.app import app
        assert app is not None

    def test_app_is_fastapi(self):
        """验证 app 是 FastAPI 实例"""
        from src.interfaces.api.app import app
        from fastapi import FastAPI
        assert isinstance(app, FastAPI)


class TestWorkerAPIEndpoints:
    """Worker API 端点测试"""

    @pytest.fixture
    def client(self):
        """创建测试客户端"""
        from src.interfaces.api.app import app
        from src.interfaces.api.dependencies.worker_dependencies import use_in_memory_stores

        # 确保使用内存数据库
        use_in_memory_stores()

        return TestClient(app)

    def test_create_worker_endpoint_exists(self, client):
        """验证 POST /v1/workers 端点存在"""
        # 使用 Stage 1 扁平化 API 格式
        worker_data = {
            "id": "wrk_test_001",
            "type": "bot",
            "name": "Test Bot",
            "handle": "@test",
            "responsibilities": ["test"],
            "capabilities": [{"name": "test", "level": "expert"}],
            "availability": "available",
            "trust_level": "trusted"
        }
        response = client.post("/v1/workers", json=worker_data)
        # 应该返回 201
        assert response.status_code == 201

    def test_create_worker_returns_worker(self, client):
        """验证创建 Worker 返回 Worker 数据"""
        worker_data = {
            "id": "wrk_test_002",
            "type": "bot",
            "name": "Test Bot 2",
            "handle": "@test2",
            "responsibilities": ["test"],
            "capabilities": [{"name": "test", "level": "expert"}],
            "availability": "available",
            "trust_level": "trusted"
        }
        response = client.post("/v1/workers", json=worker_data)
        assert response.status_code == 201

        data = response.json()
        assert data["id"] == "wrk_test_002"
        assert data["type"] == "bot"
        assert data["name"] == "Test Bot 2"

    def test_list_workers_endpoint_exists(self, client):
        """验证 GET /v1/workers 端点存在"""
        response = client.get("/v1/workers")
        assert response.status_code == 200

    def test_list_workers_returns_items(self, client):
        """验证列出 Worker 返回 items 列表"""
        # 先创建一个
        worker_data = {
            "id": "wrk_test_003",
            "type": "bot",
            "name": "Test Bot 3",
            "handle": "@test3",
            "responsibilities": ["test"],
            "capabilities": [{"name": "test", "level": "expert"}],
            "availability": "available",
            "trust_level": "trusted"
        }
        client.post("/v1/workers", json=worker_data)

        response = client.get("/v1/workers")
        assert response.status_code == 200

        data = response.json()
        assert "items" in data
        assert isinstance(data["items"], list)

    def test_get_worker_endpoint_exists(self, client):
        """验证 GET /v1/workers/{workerId} 端点存在"""
        # 先创建一个
        worker_data = {
            "id": "wrk_test_004",
            "type": "bot",
            "name": "Test Bot 4",
            "handle": "@test4",
            "responsibilities": ["test"],
            "capabilities": [{"name": "test", "level": "expert"}],
            "availability": "available",
            "trust_level": "trusted"
        }
        client.post("/v1/workers", json=worker_data)

        response = client.get("/v1/workers/wrk_test_004")
        assert response.status_code == 200

    def test_get_worker_returns_worker(self, client):
        """验证获取 Worker 返回 Worker 数据"""
        # 先创建一个
        worker_data = {
            "id": "wrk_test_005",
            "type": "bot",
            "name": "Test Bot 5",
            "handle": "@test5",
            "responsibilities": ["test"],
            "capabilities": [{"name": "test", "level": "expert"}],
            "availability": "available",
            "trust_level": "trusted"
        }
        client.post("/v1/workers", json=worker_data)

        response = client.get("/v1/workers/wrk_test_005")
        assert response.status_code == 200

        data = response.json()
        assert data["id"] == "wrk_test_005"
        assert data["name"] == "Test Bot 5"

    def test_get_nonexistent_worker_returns_404(self, client):
        """验证获取不存在的 Worker 返回 404"""
        response = client.get("/v1/workers/wrk_nonexistent")
        assert response.status_code == 404

    def test_update_worker_endpoint_exists(self, client):
        """验证 PUT /v1/workers/{workerId}/online 端点存在"""
        # 先创建一个
        worker_data = {
            "id": "wrk_test_006",
            "type": "bot",
            "name": "Test Bot 6",
            "handle": "@test6",
            "responsibilities": ["test"],
            "capabilities": [{"name": "test", "level": "expert"}],
            "availability": "available",
            "trust_level": "trusted"
        }
        client.post("/v1/workers", json=worker_data)

        # Stage 1: 使用 online/offline 端点
        response = client.put("/v1/workers/wrk_test_006/online")
        assert response.status_code == 200

    def test_update_worker_returns_updated_worker(self, client):
        """验证设置 online 返回更新后的数据"""
        # 先创建一个
        worker_data = {
            "id": "wrk_test_007",
            "type": "bot",
            "name": "Test Bot 7",
            "handle": "@test7",
            "responsibilities": ["test"],
            "capabilities": [{"name": "test", "level": "expert"}],
            "availability": "available",
            "trust_level": "trusted"
        }
        client.post("/v1/workers", json=worker_data)

        # 设置 online
        response = client.put("/v1/workers/wrk_test_007/online")
        assert response.status_code == 200

        data = response.json()
        assert data["runtime_state"] == "online"


class TestWorkerAPIFiltering:
    """Worker API 筛选测试"""

    @pytest.fixture
    def client(self):
        from src.interfaces.api.app import app
        from src.interfaces.api.dependencies.worker_dependencies import use_in_memory_stores

        # 确保使用内存数据库
        use_in_memory_stores()

        return TestClient(app)

    def test_list_workers_filter_by_type(self, client):
        """验证可以按类型筛选 Worker"""
        # 创建 bot
        client.post("/v1/workers", json={
            "id": "wrk_bot_filter_test",
            "type": "bot",
            "name": "Bot",
            "handle": "@bot-filter-test",
            "responsibilities": ["test"],
            "capabilities": [{"name": "test", "level": "expert"}],
            "availability": "available",
            "trust_level": "trusted"
        })

        # 创建 human
        client.post("/v1/workers", json={
            "id": "wrk_human_filter_test",
            "type": "human",
            "name": "Human",
            "handle": "@human-filter-test",
            "responsibilities": ["review"],
            "capabilities": [{"name": "review", "level": "expert"}],
            "availability": "available",
            "trust_level": "trusted"
        })

        # 筛选 bot
        response = client.get("/v1/workers?type=bot")
        assert response.status_code == 200
        data = response.json()
        # 验证我们创建的 bot 在结果中
        bot_ids = [item["id"] for item in data["items"]]
        assert "wrk_bot_filter_test" in bot_ids

        # 筛选 human
        response = client.get("/v1/workers?type=human")
        assert response.status_code == 200
        data = response.json()
        human_ids = [item["id"] for item in data["items"]]
        assert "wrk_human_filter_test" in human_ids


class TestWorkerAPIFilteringExtended:
    """Worker API 扩展筛选测试

    Stage 1: 验证 capability/skill/resource 筛选 API。
    """

    @pytest.fixture
    def client_with_workers(self):
        """创建包含多个 Worker 的测试客户端"""
        from src.interfaces.api.app import app
        from fastapi.testclient import TestClient
        from src.interfaces.api.dependencies.worker_dependencies import use_in_memory_stores

        # 确保使用内存数据库
        use_in_memory_stores()

        client = TestClient(app)

        # Worker 1: bot, coding 能力, python skill, db resource
        client.post("/v1/workers", json={
            "id": "wrk_api_filter_ext_001",
            "type": "bot",
            "name": "Coder Bot",
            "handle": "@coder-ext",
            "responsibilities": ["coding"],
            "capabilities": [
                {"name": "coding", "level": "expert"},
                {"name": "testing", "level": "intermediate"}
            ],
            "skills": [{"name": "python", "source": "builtin", "trust_level": "trusted"}],
            "resources": [{"id": "res_db_ext_001", "kind": "dataset", "name": "DB", "access": "read"}],
            "availability": "available",
            "trust_level": "trusted"
        })

        # Worker 2: human, testing 能力, python + js skills, api resource
        client.post("/v1/workers", json={
            "id": "wrk_api_filter_ext_002",
            "type": "human",
            "name": "Reviewer",
            "handle": "@reviewer-ext",
            "responsibilities": ["review"],
            "capabilities": [
                {"name": "testing", "level": "expert"},
                {"name": "review", "level": "expert"}
            ],
            "skills": [
                {"name": "python", "source": "builtin", "trust_level": "trusted"},
                {"name": "javascript", "source": "builtin", "trust_level": "trusted"}
            ],
            "resources": [{"id": "res_api_ext_001", "kind": "api", "name": "API", "access": "read"}],
            "availability": "available",
            "trust_level": "trusted"
        })

        # Worker 3: human, design 能力, figma skill, 无 resource
        client.post("/v1/workers", json={
            "id": "wrk_api_filter_ext_003",
            "type": "human",
            "name": "Designer",
            "handle": "@designer-ext",
            "responsibilities": ["design"],
            "capabilities": [{"name": "design", "level": "expert"}],
            "skills": [{"name": "figma", "source": "plugin", "trust_level": "guarded"}],
            "resources": [],
            "availability": "available",
            "trust_level": "trusted"
        })

        return client

    # ==================== Capability 筛选测试 ====================

    def test_filter_by_single_capability(self, client_with_workers):
        """验证可以按单个 capability 筛选"""
        response = client_with_workers.get("/v1/workers?capability=coding")

        assert response.status_code == 200
        data = response.json()
        # 验证我们创建的 worker 在结果中
        worker_ids = {item["id"] for item in data["items"]}
        assert "wrk_api_filter_ext_001" in worker_ids

    def test_filter_by_multiple_capabilities(self, client_with_workers):
        """验证多个 capability 使用 OR 语义"""
        response = client_with_workers.get("/v1/workers?capability=coding&capability=review")

        assert response.status_code == 200
        data = response.json()
        worker_ids = {item["id"] for item in data["items"]}
        assert "wrk_api_filter_ext_001" in worker_ids  # 有 coding capability
        assert "wrk_api_filter_ext_002" in worker_ids  # 有 review capability

    def test_filter_by_capability_no_match(self, client_with_workers):
        """验证 capability 筛选无匹配时返回空列表或不含我们的 worker"""
        response = client_with_workers.get("/v1/workers?capability=nonexistent_capability_ext")

        assert response.status_code == 200
        data = response.json()
        # 我们创建的 worker 不应该匹配
        worker_ids = {item["id"] for item in data["items"]}
        assert "wrk_api_filter_ext_001" not in worker_ids
        assert "wrk_api_filter_ext_002" not in worker_ids
        assert "wrk_api_filter_ext_003" not in worker_ids

    # ==================== Skill 筛选测试 ====================

    def test_filter_by_single_skill(self, client_with_workers):
        """验证可以按单个 skill 筛选"""
        response = client_with_workers.get("/v1/workers?skill=python")

        assert response.status_code == 200
        data = response.json()
        worker_ids = {item["id"] for item in data["items"]}
        assert "wrk_api_filter_ext_001" in worker_ids
        assert "wrk_api_filter_ext_002" in worker_ids

    def test_filter_by_multiple_skills(self, client_with_workers):
        """验证多个 skill 使用 OR 语义"""
        response = client_with_workers.get("/v1/workers?skill=figma&skill=javascript")

        assert response.status_code == 200
        data = response.json()
        worker_ids = {item["id"] for item in data["items"]}
        assert "wrk_api_filter_ext_002" in worker_ids
        assert "wrk_api_filter_ext_003" in worker_ids

    def test_filter_by_skill_no_match(self, client_with_workers):
        """验证 skill 筛选无匹配时返回空列表或不含我们的 worker"""
        response = client_with_workers.get("/v1/workers?skill=nonexistent_skill_ext")

        assert response.status_code == 200
        data = response.json()
        worker_ids = {item["id"] for item in data["items"]}
        assert "wrk_api_filter_ext_001" not in worker_ids
        assert "wrk_api_filter_ext_002" not in worker_ids
        assert "wrk_api_filter_ext_003" not in worker_ids

    # ==================== Resource 筛选测试 ====================

    def test_filter_by_single_resource(self, client_with_workers):
        """验证可以按单个 resource 筛选"""
        response = client_with_workers.get("/v1/workers?resource=res_db_ext_001")

        assert response.status_code == 200
        data = response.json()
        worker_ids = {item["id"] for item in data["items"]}
        assert "wrk_api_filter_ext_001" in worker_ids

    def test_filter_by_multiple_resources(self, client_with_workers):
        """验证多个 resource 使用 OR 语义"""
        response = client_with_workers.get("/v1/workers?resource=res_db_ext_001&resource=res_api_ext_001")

        assert response.status_code == 200
        data = response.json()
        worker_ids = {item["id"] for item in data["items"]}
        assert "wrk_api_filter_ext_001" in worker_ids
        assert "wrk_api_filter_ext_002" in worker_ids

    def test_filter_by_resource_no_match(self, client_with_workers):
        """验证 resource 筛选无匹配时返回空列表"""
        response = client_with_workers.get("/v1/workers?resource=res_nonexistent_ext")

        assert response.status_code == 200
        data = response.json()
        worker_ids = {item["id"] for item in data["items"]}
        assert "wrk_api_filter_ext_001" not in worker_ids
        assert "wrk_api_filter_ext_002" not in worker_ids
        assert "wrk_api_filter_ext_003" not in worker_ids

    # ==================== 组合筛选测试 ====================

    def test_filter_combined_type_and_capability(self, client_with_workers):
        """验证 type + capability 组合筛选"""
        response = client_with_workers.get("/v1/workers?type=bot&capability=testing")

        assert response.status_code == 200
        data = response.json()
        worker_ids = {item["id"] for item in data["items"]}
        assert "wrk_api_filter_ext_001" in worker_ids

    def test_filter_combined_capability_and_skill(self, client_with_workers):
        """验证 capability + skill 组合筛选"""
        response = client_with_workers.get("/v1/workers?capability=coding&skill=python")

        assert response.status_code == 200
        data = response.json()
        worker_ids = {item["id"] for item in data["items"]}
        assert "wrk_api_filter_ext_001" in worker_ids

    def test_filter_combined_all_dimensions(self, client_with_workers):
        """验证所有维度组合筛选"""
        response = client_with_workers.get(
            "/v1/workers?type=human&capability=testing&skill=python&resource=res_api_ext_001"
        )

        assert response.status_code == 200
        data = response.json()
        worker_ids = {item["id"] for item in data["items"]}
        assert "wrk_api_filter_ext_002" in worker_ids

    def test_filter_combined_no_match(self, client_with_workers):
        """验证组合筛选无匹配时返回空列表"""
        response = client_with_workers.get("/v1/workers?type=bot&capability=design")

        assert response.status_code == 200
        data = response.json()
        worker_ids = {item["id"] for item in data["items"]}
        assert "wrk_api_filter_ext_001" not in worker_ids
        assert "wrk_api_filter_ext_002" not in worker_ids
        assert "wrk_api_filter_ext_003" not in worker_ids
