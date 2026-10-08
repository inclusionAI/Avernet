"""
WorkerRegistryService Tests

验证 WorkerRegistryService 实现。

M1: 应用层服务，编排 Worker 的 CRUD 操作。
"""

import pytest


class TestWorkerRegistryServiceCreation:
    """WorkerRegistryService 创建测试"""

    def test_service_importable(self):
        """验证 WorkerRegistryService 可导入"""
        from src.application.services.worker_registry_service import WorkerRegistryService
        assert WorkerRegistryService is not None

    def test_service_instantiation(self):
        """验证可以实例化"""
        from src.application.services.worker_registry_service import WorkerRegistryService
        from src.infra.repositories.in_memory_worker_repository import InMemoryWorkerRepository

        repo = InMemoryWorkerRepository()
        service = WorkerRegistryService(repo)
        assert service is not None


class TestWorkerRegistryServiceCreateWorker:
    """WorkerRegistryService 创建 Worker 测试"""

    @pytest.fixture
    def service(self):
        from src.application.services.worker_registry_service import WorkerRegistryService
        from src.infra.repositories.in_memory_worker_repository import InMemoryWorkerRepository

        repo = InMemoryWorkerRepository()
        return WorkerRegistryService(repo)

    def test_create_worker(self, service):
        """验证可以创建 Worker"""
        worker_data = {
            "id": "wrk_bot_001",
            "type": "bot",
            "identity": {"name": "Test Bot", "handle": "@test-bot"},
            "responsibilities": ["testing"],
            "capabilities": [{"name": "test", "level": "expert"}],
            "constraints": [],
            "skills": [],
            "resources": [],
            "state": {"availability": "available", "trust_level": "trusted"}
        }

        worker = service.create_worker(worker_data)

        assert worker.id == "wrk_bot_001"
        assert worker.type == "bot"

    def test_create_human_worker(self, service):
        """验证可以创建 human 类型 Worker"""
        worker_data = {
            "id": "wrk_human_001",
            "type": "human",
            "identity": {"name": "John Doe", "handle": "@john"},
            "responsibilities": ["review"],
            "capabilities": [{"name": "architecture", "level": "expert"}],
            "constraints": [],
            "skills": [],
            "resources": [],
            "state": {"availability": "available", "trust_level": "trusted"}
        }

        worker = service.create_worker(worker_data)

        assert worker.id == "wrk_human_001"
        assert worker.type == "human"

    def test_create_duplicate_worker_raises_error(self, service):
        """验证创建重复 Worker 抛出异常"""
        from src.domain.exceptions import DuplicateWorkerException

        worker_data = {
            "id": "wrk_bot_001",
            "type": "bot",
            "identity": {"name": "Test Bot", "handle": "@test-bot"},
            "responsibilities": ["testing"],
            "capabilities": [{"name": "test", "level": "expert"}],
            "constraints": [],
            "skills": [],
            "resources": [],
            "state": {"availability": "available", "trust_level": "trusted"}
        }

        service.create_worker(worker_data)

        with pytest.raises(DuplicateWorkerException):
            service.create_worker(worker_data)


class TestWorkerRegistryServiceGetWorker:
    """WorkerRegistryService 获取 Worker 测试"""

    @pytest.fixture
    def service(self):
        from src.application.services.worker_registry_service import WorkerRegistryService
        from src.infra.repositories.in_memory_worker_repository import InMemoryWorkerRepository

        repo = InMemoryWorkerRepository()
        return WorkerRegistryService(repo)

    def test_get_worker_by_id(self, service):
        """验证可以通过 ID 获取 Worker"""
        worker_data = {
            "id": "wrk_bot_001",
            "type": "bot",
            "identity": {"name": "Test Bot", "handle": "@test-bot"},
            "responsibilities": ["testing"],
            "capabilities": [{"name": "test", "level": "expert"}],
            "constraints": [],
            "skills": [],
            "resources": [],
            "state": {"availability": "available", "trust_level": "trusted"}
        }
        service.create_worker(worker_data)

        worker = service.get_worker("wrk_bot_001")

        assert worker is not None
        assert worker.id == "wrk_bot_001"

    def test_get_nonexistent_worker_returns_none(self, service):
        """验证获取不存在的 Worker 返回 None"""
        worker = service.get_worker("wrk_nonexistent")

        assert worker is None


class TestWorkerRegistryServiceListWorkers:
    """WorkerRegistryService 列出 Worker 测试"""

    @pytest.fixture
    def service(self):
        from src.application.services.worker_registry_service import WorkerRegistryService
        from src.infra.repositories.in_memory_worker_repository import InMemoryWorkerRepository

        repo = InMemoryWorkerRepository()
        return WorkerRegistryService(repo)

    def test_list_all_workers(self, service):
        """验证可以列出所有 Worker"""
        service.create_worker({
            "id": "wrk_bot_001",
            "type": "bot",
            "identity": {"name": "Bot 1", "handle": "@bot1"},
            "responsibilities": ["testing"],
            "capabilities": [{"name": "test", "level": "expert"}],
            "constraints": [],
            "skills": [],
            "resources": [],
            "state": {"availability": "available", "trust_level": "trusted"}
        })
        service.create_worker({
            "id": "wrk_bot_002",
            "type": "bot",
            "identity": {"name": "Bot 2", "handle": "@bot2"},
            "responsibilities": ["testing"],
            "capabilities": [{"name": "test", "level": "expert"}],
            "constraints": [],
            "skills": [],
            "resources": [],
            "state": {"availability": "available", "trust_level": "trusted"}
        })

        workers = service.list_workers()

        assert len(workers) == 2

    def test_list_workers_filter_by_type(self, service):
        """验证可以按类型筛选"""
        service.create_worker({
            "id": "wrk_bot_001",
            "type": "bot",
            "identity": {"name": "Bot 1", "handle": "@bot1"},
            "responsibilities": ["testing"],
            "capabilities": [{"name": "test", "level": "expert"}],
            "constraints": [],
            "skills": [],
            "resources": [],
            "state": {"availability": "available", "trust_level": "trusted"}
        })
        service.create_worker({
            "id": "wrk_human_001",
            "type": "human",
            "identity": {"name": "Human 1", "handle": "@human1"},
            "responsibilities": ["review"],
            "capabilities": [{"name": "review", "level": "expert"}],
            "constraints": [],
            "skills": [],
            "resources": [],
            "state": {"availability": "available", "trust_level": "trusted"}
        })

        bots = service.list_workers(type="bot")
        humans = service.list_workers(type="human")

        assert len(bots) == 1
        assert len(humans) == 1


class TestWorkerRegistryServiceUpdateWorker:
    """WorkerRegistryService 更新 Worker 测试"""

    @pytest.fixture
    def service(self):
        from src.application.services.worker_registry_service import WorkerRegistryService
        from src.infra.repositories.in_memory_worker_repository import InMemoryWorkerRepository

        repo = InMemoryWorkerRepository()
        return WorkerRegistryService(repo)

    def test_update_worker(self, service):
        """验证可以更新 Worker"""
        service.create_worker({
            "id": "wrk_bot_001",
            "type": "bot",
            "identity": {"name": "Test Bot", "handle": "@test-bot"},
            "responsibilities": ["testing"],
            "capabilities": [{"name": "test", "level": "expert"}],
            "constraints": [],
            "skills": [],
            "resources": [],
            "state": {"availability": "available", "trust_level": "trusted"}
        })

        updated = service.update_worker("wrk_bot_001", {
            "identity": {"name": "Updated Bot", "handle": "@updated"}
        })

        assert updated.identity.name == "Updated Bot"

    def test_update_nonexistent_worker_raises_error(self, service):
        """验证更新不存在的 Worker 抛出异常"""
        from src.domain.exceptions import WorkerNotFoundException

        with pytest.raises(WorkerNotFoundException):
            service.update_worker("wrk_nonexistent", {"identity": {"name": "Updated", "handle": "@updated"}})


class TestWorkerRegistryServiceFiltering:
    """WorkerRegistryService 筛选测试

    M1: 验证 capability/skill/resource 筛选能力通过 service 层正确传递。
    """

    @pytest.fixture
    def service_with_workers(self):
        """创建包含多个 Worker 的 service"""
        from src.application.services.worker_registry_service import WorkerRegistryService
        from src.infra.repositories.in_memory_worker_repository import InMemoryWorkerRepository

        repo = InMemoryWorkerRepository()
        service = WorkerRegistryService(repo)

        # Worker 1: bot, coding 能力, python skill
        service.create_worker({
            "id": "wrk_svc_001",
            "type": "bot",
            "identity": {"name": "Coder Bot", "handle": "@coder"},
            "responsibilities": ["coding"],
            "capabilities": [
                {"name": "coding", "level": "expert"},
                {"name": "testing", "level": "intermediate"}
            ],
            "constraints": [],
            "skills": [{"name": "python", "source": "builtin", "trust_level": "trusted"}],
            "resources": [{"id": "res_db_001", "kind": "dataset", "name": "DB", "access": "read"}],
            "state": {"availability": "available", "trust_level": "trusted"}
        })

        # Worker 2: human, review 能力, python + js skills
        service.create_worker({
            "id": "wrk_svc_002",
            "type": "human",
            "identity": {"name": "Reviewer", "handle": "@reviewer"},
            "responsibilities": ["review"],
            "capabilities": [
                {"name": "testing", "level": "expert"},
                {"name": "review", "level": "expert"}
            ],
            "constraints": [],
            "skills": [
                {"name": "python", "source": "builtin", "trust_level": "trusted"},
                {"name": "javascript", "source": "builtin", "trust_level": "trusted"}
            ],
            "resources": [{"id": "res_api_001", "kind": "api", "name": "API", "access": "read"}],
            "state": {"availability": "available", "trust_level": "trusted"}
        })

        # Worker 3: human, design 能力, figma skill
        service.create_worker({
            "id": "wrk_svc_003",
            "type": "human",
            "identity": {"name": "Designer", "handle": "@designer"},
            "responsibilities": ["design"],
            "capabilities": [{"name": "design", "level": "expert"}],
            "constraints": [],
            "skills": [{"name": "figma", "source": "plugin", "trust_level": "guarded"}],
            "resources": [],
            "state": {"availability": "available", "trust_level": "trusted"}
        })

        return service

    # ==================== Capability 筛选测试 ====================

    def test_list_workers_filter_by_capability(self, service_with_workers):
        """验证可以通过 capability 筛选"""
        workers = service_with_workers.list_workers(capabilities=["coding"])

        assert len(workers) == 1
        assert workers[0].id == "wrk_svc_001"

    def test_list_workers_filter_by_multiple_capabilities(self, service_with_workers):
        """验证多个 capability 使用 OR 语义"""
        workers = service_with_workers.list_workers(capabilities=["coding", "review"])

        assert len(workers) == 2
        worker_ids = {w.id for w in workers}
        assert "wrk_svc_001" in worker_ids
        assert "wrk_svc_002" in worker_ids

    # ==================== Skill 筛选测试 ====================

    def test_list_workers_filter_by_skill(self, service_with_workers):
        """验证可以通过 skill 筛选"""
        workers = service_with_workers.list_workers(skills=["python"])

        assert len(workers) == 2
        worker_ids = {w.id for w in workers}
        assert "wrk_svc_001" in worker_ids
        assert "wrk_svc_002" in worker_ids

    def test_list_workers_filter_by_multiple_skills(self, service_with_workers):
        """验证多个 skill 使用 OR 语义"""
        workers = service_with_workers.list_workers(skills=["figma", "javascript"])

        assert len(workers) == 2
        worker_ids = {w.id for w in workers}
        assert "wrk_svc_002" in worker_ids
        assert "wrk_svc_003" in worker_ids

    # ==================== Resource 筛选测试 ====================

    def test_list_workers_filter_by_resource(self, service_with_workers):
        """验证可以通过 resource 筛选"""
        workers = service_with_workers.list_workers(resources=["res_db_001"])

        assert len(workers) == 1
        assert workers[0].id == "wrk_svc_001"

    def test_list_workers_filter_by_multiple_resources(self, service_with_workers):
        """验证多个 resource 使用 OR 语义"""
        workers = service_with_workers.list_workers(resources=["res_db_001", "res_api_001"])

        assert len(workers) == 2
        worker_ids = {w.id for w in workers}
        assert "wrk_svc_001" in worker_ids
        assert "wrk_svc_002" in worker_ids

    # ==================== 组合筛选测试 ====================

    def test_list_workers_filter_combined_type_and_capability(self, service_with_workers):
        """验证 type + capability 组合筛选"""
        workers = service_with_workers.list_workers(type="bot", capabilities=["testing"])

        assert len(workers) == 1
        assert workers[0].id == "wrk_svc_001"

    def test_list_workers_filter_combined_capability_and_skill(self, service_with_workers):
        """验证 capability + skill 组合筛选"""
        workers = service_with_workers.list_workers(
            capabilities=["coding"],
            skills=["python"]
        )

        assert len(workers) == 1
        assert workers[0].id == "wrk_svc_001"

    def test_list_workers_filter_combined_all_dimensions(self, service_with_workers):
        """验证所有维度组合筛选"""
        workers = service_with_workers.list_workers(
            type="human",
            capabilities=["testing"],
            skills=["python"],
            resources=["res_api_001"]
        )

        assert len(workers) == 1
        assert workers[0].id == "wrk_svc_002"

    def test_list_workers_filter_no_match(self, service_with_workers):
        """验证筛选无匹配时返回空列表"""
        workers = service_with_workers.list_workers(capabilities=["nonexistent"])

        assert len(workers) == 0