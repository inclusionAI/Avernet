"""
InMemoryWorkerRepository Tests

验证 InMemoryWorkerRepository 实现。

M1: 内存存储实现，供测试和开发使用。

NOTE: 这是 FAKE/PLACEHOLDER 实现，不用于生产环境。
"""

import pytest


class TestInMemoryWorkerRepositoryCreation:
    """InMemoryWorkerRepository 创建测试"""

    def test_repository_importable(self):
        """验证 InMemoryWorkerRepository 可导入"""
        from src.infra.repositories.in_memory_worker_repository import InMemoryWorkerRepository
        assert InMemoryWorkerRepository is not None

    def test_repository_instantiation(self):
        """验证可以实例化"""
        from src.infra.repositories.in_memory_worker_repository import InMemoryWorkerRepository

        repo = InMemoryWorkerRepository()
        assert repo is not None

    def test_repository_implements_interface(self):
        """验证实现了 WorkerRepository 接口"""
        from src.infra.repositories.in_memory_worker_repository import InMemoryWorkerRepository
        from src.domain.services.worker_repository import WorkerRepository

        repo = InMemoryWorkerRepository()
        assert isinstance(repo, WorkerRepository)


class TestInMemoryWorkerRepositoryCRUD:
    """InMemoryWorkerRepository CRUD 操作测试"""

    @pytest.fixture
    def repo(self):
        """创建空仓库"""
        from src.infra.repositories.in_memory_worker_repository import InMemoryWorkerRepository
        return InMemoryWorkerRepository()

    @pytest.fixture
    def sample_worker(self):
        """创建示例 Worker"""
        from src.domain.models.worker import Worker

        return Worker(
            id="wrk_test_001",
            type="bot",
            identity={"name": "Test Bot", "handle": "@test-bot"},
            responsibilities=["testing"],
            capabilities=[{"name": "test", "level": "expert"}],
            constraints=[],
            skills=[],
            resources=[],
            state={"availability": "available", "trust_level": "trusted"}
        )

    def test_create_worker(self, repo, sample_worker):
        """验证可以创建 Worker"""
        created = repo.create(sample_worker)

        assert created.id == sample_worker.id
        assert created.type == sample_worker.type

    def test_get_by_id_returns_worker(self, repo, sample_worker):
        """验证可以通过 ID 获取 Worker"""
        repo.create(sample_worker)

        found = repo.get_by_id("wrk_test_001")

        assert found is not None
        assert found.id == "wrk_test_001"

    def test_get_by_id_returns_none_if_not_found(self, repo):
        """验证找不到 Worker 时返回 None"""
        found = repo.get_by_id("wrk_nonexistent")

        assert found is None

    def test_list_returns_all_workers(self, repo, sample_worker):
        """验证 list 返回所有 Worker"""
        repo.create(sample_worker)

        worker2 = sample_worker.model_copy(update={"id": "wrk_test_002"})
        repo.create(worker2)

        workers = repo.list()

        assert len(workers) == 2

    def test_list_filter_by_type(self, repo, sample_worker):
        """验证可以按类型筛选"""
        repo.create(sample_worker)

        from src.domain.models.worker import Worker
        human_worker = Worker(
            id="wrk_human_001",
            type="human",
            identity={"name": "Human", "handle": "@human"},
            responsibilities=["review"],
            capabilities=[{"name": "review", "level": "expert"}],
            constraints=[],
            skills=[],
            resources=[],
            state={"availability": "available", "trust_level": "trusted"}
        )
        repo.create(human_worker)

        bots = repo.list(type="bot")
        humans = repo.list(type="human")

        assert len(bots) == 1
        assert len(humans) == 1

    def test_update_worker(self, repo, sample_worker):
        """验证可以更新 Worker"""
        repo.create(sample_worker)

        # 创建新的 Worker 对象，更新 identity
        from src.domain.models.worker import WorkerIdentity
        updated_worker = sample_worker.model_copy(deep=True)
        updated_worker.identity = WorkerIdentity(
            name="Updated Bot",
            handle="@updated"
        )
        result = repo.update(updated_worker)

        assert result.identity.name == "Updated Bot"

        # 验证持久化
        found = repo.get_by_id("wrk_test_001")
        assert found.identity.name == "Updated Bot"

    def test_delete_worker(self, repo, sample_worker):
        """验证可以删除 Worker"""
        repo.create(sample_worker)

        result = repo.delete("wrk_test_001")

        assert result is True
        assert repo.get_by_id("wrk_test_001") is None

    def test_exists_returns_true(self, repo, sample_worker):
        """验证 exists 返回 True"""
        repo.create(sample_worker)

        assert repo.exists("wrk_test_001") is True

    def test_exists_returns_false(self, repo):
        """验证 exists 返回 False"""
        assert repo.exists("wrk_nonexistent") is False


class TestInMemoryWorkerRepositoryErrors:
    """InMemoryWorkerRepository 错误处理测试"""

    @pytest.fixture
    def repo(self):
        from src.infra.repositories.in_memory_worker_repository import InMemoryWorkerRepository
        return InMemoryWorkerRepository()

    @pytest.fixture
    def sample_worker(self):
        from src.domain.models.worker import Worker
        return Worker(
            id="wrk_test_001",
            type="bot",
            identity={"name": "Test Bot", "handle": "@test-bot"},
            responsibilities=["testing"],
            capabilities=[{"name": "test", "level": "expert"}],
            constraints=[],
            skills=[],
            resources=[],
            state={"availability": "available", "trust_level": "trusted"}
        )

    def test_create_duplicate_raises_error(self, repo, sample_worker):
        """验证创建重复 Worker 抛出异常"""
        from src.domain.exceptions import DuplicateWorkerException

        repo.create(sample_worker)

        with pytest.raises(DuplicateWorkerException):
            repo.create(sample_worker)

    def test_update_nonexistent_raises_error(self, repo, sample_worker):
        """验证更新不存在的 Worker 抛出异常"""
        from src.domain.exceptions import WorkerNotFoundException

        with pytest.raises(WorkerNotFoundException):
            repo.update(sample_worker)

    def test_delete_nonexistent_raises_error(self, repo):
        """验证删除不存在的 Worker 抛出异常"""
        from src.domain.exceptions import WorkerNotFoundException

        with pytest.raises(WorkerNotFoundException):
            repo.delete("wrk_nonexistent")


class TestInMemoryWorkerRepositoryFiltering:
    """InMemoryWorkerRepository 筛选测试

    M1: 验证 capability/skill/resource 筛选能力。

    筛选语义：
    - 不同筛选维度之间使用 AND 语义
    - 同一筛选维度内，如果传入多个值，使用 OR 语义
    - 未传该筛选条件时，不对该维度做过滤
    - 空列表不做过滤（不返回空结果）
    """

    @pytest.fixture
    def repo_with_workers(self):
        """创建包含多个 Worker 的仓库"""
        from src.infra.repositories.in_memory_worker_repository import InMemoryWorkerRepository
        from src.domain.models.worker import Worker

        repo = InMemoryWorkerRepository()

        # Worker 1: 有 coding 和 testing 能力，有 python skill，有db resource
        repo.create(Worker(
            id="wrk_filter_001",
            type="bot",
            identity={"name": "Coder Bot", "handle": "@coder"},
            responsibilities=["coding"],
            capabilities=[
                {"name": "coding", "level": "expert"},
                {"name": "testing", "level": "intermediate"}
            ],
            constraints=[],
            skills=[{"name": "python", "source": "builtin", "trust_level": "trusted"}],
            resources=[{"id": "res_db_001", "kind": "dataset", "name": "DB", "access": "read"}],
            state={"availability": "available", "trust_level": "trusted"}
        ))

        # Worker 2: 有 testing 和 review 能力，有 python 和 js skill，有 api resource
        repo.create(Worker(
            id="wrk_filter_002",
            type="human",
            identity={"name": "Reviewer", "handle": "@reviewer"},
            responsibilities=["review"],
            capabilities=[
                {"name": "testing", "level": "expert"},
                {"name": "review", "level": "expert"}
            ],
            constraints=[],
            skills=[
                {"name": "python", "source": "builtin", "trust_level": "trusted"},
                {"name": "javascript", "source": "builtin", "trust_level": "trusted"}
            ],
            resources=[{"id": "res_api_001", "kind": "api", "name": "API", "access": "read"}],
            state={"availability": "available", "trust_level": "trusted"}
        ))

        # Worker 3: 有 design 能力，有 figma skill，无 resource
        repo.create(Worker(
            id="wrk_filter_003",
            type="human",
            identity={"name": "Designer", "handle": "@designer"},
            responsibilities=["design"],
            capabilities=[{"name": "design", "level": "expert"}],
            constraints=[],
            skills=[{"name": "figma", "source": "plugin", "trust_level": "guarded"}],
            resources=[],
            state={"availability": "available", "trust_level": "trusted"}
        ))

        return repo

    # ==================== Capability 筛选测试 ====================

    def test_filter_by_single_capability(self, repo_with_workers):
        """验证可以按单个 capability 筛选（OR 语义）"""
        workers = repo_with_workers.list(capabilities=["coding"])

        assert len(workers) == 1
        assert workers[0].id == "wrk_filter_001"

    def test_filter_by_multiple_capabilities_or_semantics(self, repo_with_workers):
        """验证多个 capability 使用 OR 语义"""
        # coding 或 review
        workers = repo_with_workers.list(capabilities=["coding", "review"])

        # 应该返回 Worker 1 (有 coding) 和 Worker 2 (有 review)
        assert len(workers) == 2
        worker_ids = {w.id for w in workers}
        assert "wrk_filter_001" in worker_ids
        assert "wrk_filter_002" in worker_ids

    def test_filter_by_capability_no_match(self, repo_with_workers):
        """验证 capability 筛选无匹配时返回空列表"""
        workers = repo_with_workers.list(capabilities=["nonexistent_capability"])

        assert len(workers) == 0

    def test_filter_by_capability_empty_list_no_filter(self, repo_with_workers):
        """验证空 capability 列表不做过滤"""
        workers = repo_with_workers.list(capabilities=[])

        # 应该返回所有 Worker
        assert len(workers) == 3

    # ==================== Skill 筛选测试 ====================

    def test_filter_by_single_skill(self, repo_with_workers):
        """验证可以按单个 skill 筛选"""
        workers = repo_with_workers.list(skills=["python"])

        # Worker 1 和 Worker 2 都有 python skill
        assert len(workers) == 2
        worker_ids = {w.id for w in workers}
        assert "wrk_filter_001" in worker_ids
        assert "wrk_filter_002" in worker_ids

    def test_filter_by_multiple_skills_or_semantics(self, repo_with_workers):
        """验证多个 skill 使用 OR 语义"""
        # figma 或 javascript
        workers = repo_with_workers.list(skills=["figma", "javascript"])

        # 应该返回 Worker 2 (有 javascript) 和 Worker 3 (有 figma)
        assert len(workers) == 2
        worker_ids = {w.id for w in workers}
        assert "wrk_filter_002" in worker_ids
        assert "wrk_filter_003" in worker_ids

    def test_filter_by_skill_no_match(self, repo_with_workers):
        """验证 skill 筛选无匹配时返回空列表"""
        workers = repo_with_workers.list(skills=["nonexistent_skill"])

        assert len(workers) == 0

    def test_filter_by_skill_empty_list_no_filter(self, repo_with_workers):
        """验证空 skill 列表不做过滤"""
        workers = repo_with_workers.list(skills=[])

        assert len(workers) == 3

    # ==================== Resource 筛选测试 ====================

    def test_filter_by_single_resource(self, repo_with_workers):
        """验证可以按单个 resource 筛选"""
        workers = repo_with_workers.list(resources=["res_db_001"])

        assert len(workers) == 1
        assert workers[0].id == "wrk_filter_001"

    def test_filter_by_multiple_resources_or_semantics(self, repo_with_workers):
        """验证多个 resource 使用 OR 语义"""
        workers = repo_with_workers.list(resources=["res_db_001", "res_api_001"])

        assert len(workers) == 2
        worker_ids = {w.id for w in workers}
        assert "wrk_filter_001" in worker_ids
        assert "wrk_filter_002" in worker_ids

    def test_filter_by_resource_no_match(self, repo_with_workers):
        """验证 resource 筛选无匹配时返回空列表"""
        workers = repo_with_workers.list(resources=["res_nonexistent"])

        assert len(workers) == 0

    def test_filter_by_resource_empty_list_no_filter(self, repo_with_workers):
        """验证空 resource 列表不做过滤"""
        workers = repo_with_workers.list(resources=[])

        assert len(workers) == 3

    # ==================== 组合筛选测试 (AND语义) ====================

    def test_filter_by_type_and_capability(self, repo_with_workers):
        """验证 type + capability 组合筛选（AND 语义）"""
        # bot 类型 + testing 能力
        workers = repo_with_workers.list(type="bot", capabilities=["testing"])

        assert len(workers) == 1
        assert workers[0].id == "wrk_filter_001"

    def test_filter_by_capability_and_skill(self, repo_with_workers):
        """验证 capability + skill 组合筛选（AND 语义）"""
        # coding 能力 + python skill
        workers = repo_with_workers.list(capabilities=["coding"], skills=["python"])

        assert len(workers) == 1
        assert workers[0].id == "wrk_filter_001"

    def test_filter_by_all_dimensions(self, repo_with_workers):
        """验证所有维度组合筛选（AND 语义）"""
        # human 类型 + testing 能力 + python skill + api resource
        workers = repo_with_workers.list(
            type="human",
            capabilities=["testing"],
            skills=["python"],
            resources=["res_api_001"]
        )

        assert len(workers) == 1
        assert workers[0].id == "wrk_filter_002"

    def test_filter_combined_no_match(self, repo_with_workers):
        """验证组合筛选无匹配时返回空列表"""
        # bot 类型 + design 能力（不存在这样的 Worker）
        workers = repo_with_workers.list(type="bot", capabilities=["design"])

        assert len(workers) == 0