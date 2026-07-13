"""
Profile API MVP Tests

测试覆盖：
1. Domain model tests
2. SQLite adapter tests
3. Service tests
4. API integration tests
"""

import pytest
from datetime import datetime

from src.domain.models.worker_profile_content import (
    ProfileContentType,
    SkillSet,
    WorkerProfileContent,
    WorkerProfileContentList,
)
from src.domain.models.worker_profile import SourceType
from src.infra.adapters.sqlite_worker_profile_content_store import SQLiteWorkerProfileContentStore
from src.application.services.worker_profile_content_service import WorkerProfileContentService
from src.infra.worker_profiles.sources.api_worker_profile_source import ApiWorkerProfileSource


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
def store():
    """创建内存存储"""
    return SQLiteWorkerProfileContentStore(":memory:")


@pytest.fixture
def service(store):
    """创建服务"""
    return WorkerProfileContentService(store)


# ============================================================================
# Domain Model Tests
# ============================================================================

class TestWorkerProfileContent:
    """WorkerProfileContent 模型测试"""

    def test_create_minimal_content(self):
        """测试创建最小内容"""
        content = WorkerProfileContent(
            worker_id="wrk_test_001",
            profile_id="default",
        )

        assert content.worker_id == "wrk_test_001"
        assert content.profile_id == "default"
        assert content.profile_key == "wrk_test_001:default"
        assert content.content_type == ProfileContentType.API
        assert content.is_active is False
        assert content.version == 1

    def test_create_full_content(self):
        """测试创建完整内容"""
        content = WorkerProfileContent(
            worker_id="wrk_dba_001",
            profile_id="dba_profile",
            display_name="张三 (DBA)",
            soul_md="# SOUL\n\n我是数据库架构师",
            agents_md="# AGENTS\n\n工作空间配置",
            tools_md="# TOOLS\n\n工具配置",
            skill_sets=[
                SkillSet(name="mysql_tuning", description="MySQL 调优"),
            ],
            metadata={"expertise": "MySQL/PostgreSQL", "years": 8},
        )

        assert content.worker_id == "wrk_dba_001"
        assert content.display_name == "张三 (DBA)"
        assert content.soul_md is not None
        assert len(content.skill_sets) == 1
        assert content.metadata["expertise"] == "MySQL/PostgreSQL"

    def test_generate_searchable_text(self):
        """测试生成可检索文本"""
        content = WorkerProfileContent(
            worker_id="wrk_test",
            profile_id="default",
            display_name="测试专家",
            soul_md="我是一名数据库专家",
            skill_sets=[
                SkillSet(name="mysql", description="MySQL 专家"),
            ],
            metadata={"domains": ["database", "performance"]},
        )

        text = content.generate_searchable_text()

        assert "[NAME:测试专家]" in text
        assert "[SOUL:" in text
        assert "[SKILL:mysql:MySQL 专家]" in text
        assert "[DOMAINS:database,performance]" in text

    def test_skill_set_validation(self):
        """测试技能集验证"""
        skill = SkillSet(
            name="python",
            description="Python 编程",
            metadata={"level": "expert"},
        )

        assert skill.name == "python"
        assert skill.description == "Python 编程"
        assert skill.metadata["level"] == "expert"


# ============================================================================
# SQLite Adapter Tests
# ============================================================================

class TestSQLiteWorkerProfileContentStore:
    """SQLite 存储适配器测试"""

    def test_save_and_get(self, store):
        """测试保存和获取"""
        content = WorkerProfileContent(
            worker_id="wrk_test",
            profile_id="default",
            display_name="测试",
            soul_md="Soul content",
        )

        # 保存
        saved = store.save(content)
        assert saved.version == 1
        assert saved.created_at is not None
        assert saved.updated_at is not None

        # 获取
        retrieved = store.get("wrk_test", "default")
        assert retrieved is not None
        assert retrieved.worker_id == "wrk_test"
        assert retrieved.display_name == "测试"
        assert retrieved.soul_md == "Soul content"

    def test_update(self, store):
        """测试更新"""
        # 第一次保存
        content = WorkerProfileContent(
            worker_id="wrk_test",
            profile_id="default",
            display_name="原名",
        )
        saved = store.save(content)
        assert saved.version == 1

        # 第二次保存（更新）
        content.display_name = "新名称"
        updated = store.save(content)
        assert updated.version == 2

        # 验证
        retrieved = store.get("wrk_test", "default")
        assert retrieved.display_name == "新名称"
        assert retrieved.version == 2

    def test_delete(self, store):
        """测试删除"""
        content = WorkerProfileContent(
            worker_id="wrk_test",
            profile_id="default",
        )
        store.save(content)

        # 删除
        result = store.delete("wrk_test", "default")
        assert result is True

        # 验证已删除
        retrieved = store.get("wrk_test", "default")
        assert retrieved is None

    def test_activate(self, store):
        """测试激活 Profile"""
        # 创建两个 Profile
        store.save(WorkerProfileContent(worker_id="wrk_test", profile_id="p1"))
        store.save(WorkerProfileContent(worker_id="wrk_test", profile_id="p2"))

        # 激活 p1
        result = store.activate("wrk_test", "p1")
        assert result is not None
        assert result.profile_id == "p1"
        assert result.is_active is True

        # 验证只有一个活跃
        active = store.get_active("wrk_test")
        assert active.profile_id == "p1"

        # 激活 p2
        store.activate("wrk_test", "p2")
        active = store.get_active("wrk_test")
        assert active.profile_id == "p2"

        # p1 应该变为非活跃
        p1 = store.get("wrk_test", "p1")
        assert p1.is_active is False

    def test_list_by_worker(self, store):
        """测试列出 Worker 的所有 Profile"""
        store.save(WorkerProfileContent(worker_id="wrk_test", profile_id="p1"))
        store.save(WorkerProfileContent(worker_id="wrk_test", profile_id="p2"))
        store.save(WorkerProfileContent(worker_id="wrk_test", profile_id="p3"))
        store.activate("wrk_test", "p2")

        result = store.list_by_worker("wrk_test")

        assert result.total == 3
        assert result.active_profile_id == "p2"

    def test_get_all_active(self, store):
        """测试获取所有活跃 Profile"""
        store.save(WorkerProfileContent(worker_id="wrk_1", profile_id="default"))
        store.save(WorkerProfileContent(worker_id="wrk_2", profile_id="default"))
        store.activate("wrk_1", "default")
        store.activate("wrk_2", "default")

        actives = store.get_all_active()

        assert len(actives) == 2
        worker_ids = [a.worker_id for a in actives]
        assert "wrk_1" in worker_ids
        assert "wrk_2" in worker_ids


# ============================================================================
# Service Tests
# ============================================================================

class TestWorkerProfileContentService:
    """服务层测试"""

    def test_register_profile(self, service):
        """测试注册 Profile"""
        content = service.register_or_update_profile(
            worker_id="wrk_dba",
            profile_id="default",
            display_name="数据库专家",
            soul_md="# SOUL\n\n我是数据库专家",
            skill_sets=[
                {"name": "mysql", "description": "MySQL 专家"},
                {"name": "redis", "description": "Redis 专家"},
            ],
            metadata={"years": 8},
        )

        assert content.worker_id == "wrk_dba"
        assert content.display_name == "数据库专家"
        assert len(content.skill_sets) == 2
        assert content.version == 1

    def test_register_and_activate(self, service):
        """测试注册并激活"""
        content = service.register_or_update_profile(
            worker_id="wrk_test",
            profile_id="default",
            display_name="测试",
            activate=True,
        )

        assert content.is_active is True

        # 验证可以通过 get_active 获取
        active = service.get_active_profile("wrk_test")
        assert active is not None
        assert active.profile_id == "default"

    def test_activate_profile(self, service):
        """测试激活 Profile"""
        service.register_or_update_profile("wrk_test", "p1")
        service.register_or_update_profile("wrk_test", "p2")

        # 激活 p1
        result = service.activate_profile("wrk_test", "p1")
        assert result is not None
        assert result.profile_id == "p1"

        # 激活 p2
        result = service.activate_profile("wrk_test", "p2")
        assert result.profile_id == "p2"

        # p1 应该不再活跃
        p1 = service.get_profile("wrk_test", "p1")
        assert p1.is_active is False

    def test_list_profiles(self, service):
        """测试列出 Profile"""
        service.register_or_update_profile("wrk_test", "p1")
        service.register_or_update_profile("wrk_test", "p2", activate=True)

        result = service.list_profiles("wrk_test")

        assert result.total == 2
        assert result.active_profile_id == "p2"

    def test_delete_profile(self, service):
        """测试删除 Profile"""
        service.register_or_update_profile("wrk_test", "default")

        result = service.delete_profile("wrk_test", "default")
        assert result is True

        # 验证已删除
        content = service.get_profile("wrk_test", "default")
        assert content is None


# ============================================================================
# Integration Tests
# ============================================================================

class TestApiWorkerProfileSource:
    """API Profile Source 与检索系统集成测试"""

    def test_convert_to_worker_profile(self, store):
        """测试转换为 WorkerProfile"""
        # 准备数据
        service = WorkerProfileContentService(store)
        service.register_or_update_profile(
            worker_id="wrk_dba",
            profile_id="default",
            display_name="张三 (DBA)",
            soul_md="# SOUL\n\n我是数据库架构师",
            agents_md="# AGENTS\n\n工作配置",
            skill_sets=[
                {"name": "mysql_tuning", "description": "MySQL 调优"},
            ],
            metadata={"domains": ["database"]},
            activate=True,
        )

        # 创建 Source
        source = ApiWorkerProfileSource(store)

        # 扫描
        result = source.scan()

        assert len(result.profiles) == 1
        profile = result.profiles[0]

        assert profile.staff_id == "wrk_dba"
        assert profile.profile_id == "default"
        assert profile.source_type == SourceType.API
        assert len(profile.context_fragments) == 2  # soul + agents
        assert len(profile.active_skills) == 1
        assert profile.searchable_text != ""

    def test_get_profile_by_staff(self, store):
        """测试按 staff_id 获取 Profile"""
        service = WorkerProfileContentService(store)
        service.register_or_update_profile(
            worker_id="wrk_test",
            profile_id="default",
            soul_md="测试内容",
            activate=True,
        )

        source = ApiWorkerProfileSource(store)

        profile = source.get_profile("wrk_test", "default")

        assert profile is not None
        assert profile.staff_id == "wrk_test"
        assert profile.context_fragments[0].content == "测试内容"


# ============================================================================
# Run Tests
# ============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v"])