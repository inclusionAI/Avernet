"""
Worker Registry Integration Tests

端到端流程测试，验证从 API 层到 Repository 层的完整链路。

Stage 1 Integration Tests:
- Worker 创建 -> 查询 -> 更新 -> 删除完整流程
- 多 Worker 场景测试
- 错误处理链路测试
- Runtime state 管理 (online/offline)

注意：Stage 1 API 使用扁平化请求格式，而非嵌套的 identity 结构。
"""



class TestWorkerRegistryServiceIntegration:
    """Worker Registry Service 层集成测试"""

    def test_service_with_repository_integration(self):
        """验证 Service 与 Repository 的集成"""
        from app.application.services.worker_registry_service import (
            WorkerRegistryService,
        )
        from app.infra.repositories.in_memory_worker_repository import (
            InMemoryWorkerRepository,
        )

        # 创建 service
        repo = InMemoryWorkerRepository()
        service = WorkerRegistryService(repo)

        # 创建 Worker - 使用扁平化数据结构
        worker_data = {
            "id": "wrk_service_test",
            "type": "bot",
            "identity": {"name": "Service Test", "handle": "@service"},
            "responsibilities": ["test"],
            "capabilities": [{"name": "test", "level": "expert"}],
            "constraints": [],
            "skills": [],
            "resources": [],
            "state": {"availability": "public", "trust_level": "trusted"},
        }

        created = service.create_worker(worker_data)
        assert created.id == "wrk_service_test"

        # 查询 Worker
        found = service.get_worker("wrk_service_test")
        assert found is not None
        assert found.identity.name == "Service Test"

        # 列出 Worker
        all_workers = service.list_workers()
        assert len(all_workers) >= 1

        # 更新 Worker
        updated = service.update_worker(
            "wrk_service_test",
            {
                "identity": {
                    "name": "Updated Service Test",
                    "handle": "@updated-service",
                }
            },
        )
        assert updated.identity.name == "Updated Service Test"

        # 验证更新持久化
        verified = service.get_worker("wrk_service_test")
        assert verified.identity.name == "Updated Service Test"
