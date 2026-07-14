"""
Tests for Skill Profile Domain Model

Worker Profile Ingestion Baseline

测试范围：
- SkillProfile: 归一化后的当前技能条目模型
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError


class TestSkillProfile:
    """测试 SkillProfile 模型"""

    def test_create_skill_profile_success(self):
        """测试创建技能档案"""
        from src.domain.models.skill_profile import SkillProfile

        skill = SkillProfile(
            name="web_search",
            description="Search the web for information",
            skill_id="search_web_v1",
            path="/skills/search/web",
            skill_set_name="default_skills",
            is_active=True,
        )

        assert skill.name == "web_search"
        assert skill.description == "Search the web for information"
        assert skill.skill_id == "search_web_v1"
        assert skill.path == "/skills/search/web"
        assert skill.skill_set_name == "default_skills"
        assert skill.is_active is True

    def test_create_skill_profile_minimal(self):
        """测试创建最小字段的技能档案"""
        from src.domain.models.skill_profile import SkillProfile

        skill = SkillProfile(
            name="test_skill",
            skill_id="test_v1",
            skill_set_name="default",
        )

        assert skill.name == "test_skill"
        assert skill.skill_id == "test_v1"
        assert skill.skill_set_name == "default"
        assert skill.description is None
        assert skill.path is None
        assert skill.is_active is True  # 默认值
        assert skill.metadata == {}  # 默认值

    def test_create_skill_profile_with_metadata(self):
        """测试创建包含元数据的技能档案"""
        from src.domain.models.skill_profile import SkillProfile

        skill = SkillProfile(
            name="code_review",
            skill_id="review_v2",
            skill_set_name="dev_tools",
            metadata={
                "category": "development",
                "version": "2.0",
                "tags": ["code", "review"],
            },
        )

        assert skill.metadata["category"] == "development"
        assert skill.metadata["version"] == "2.0"
        assert "review" in skill.metadata["tags"]

    def test_skill_profile_inactive(self):
        """测试非激活状态技能"""
        from src.domain.models.skill_profile import SkillProfile

        skill = SkillProfile(
            name="deprecated_skill",
            skill_id="old_v1",
            skill_set_name="legacy",
            is_active=False,
        )

        assert skill.is_active is False

    def test_missing_required_fields_raises_error(self):
        """测试缺少必填字段抛出错误"""
        from src.domain.models.skill_profile import SkillProfile

        # 缺少 name
        with pytest.raises(ValidationError):
            SkillProfile(
                skill_id="test_v1",
                skill_set_name="default",
            )

        # 缺少 skill_id
        with pytest.raises(ValidationError):
            SkillProfile(
                name="test_skill",
                skill_set_name="default",
            )

        # 缺少 skill_set_name
        with pytest.raises(ValidationError):
            SkillProfile(
                name="test_skill",
                skill_id="test_v1",
            )

    def test_empty_name_raises_error(self):
        """测试空名称抛出错误"""
        from src.domain.models.skill_profile import SkillProfile

        with pytest.raises(ValidationError):
            SkillProfile(
                name="",
                skill_id="test_v1",
                skill_set_name="default",
            )

    def test_empty_skill_id_raises_error(self):
        """测试空 skill_id 抛出错误"""
        from src.domain.models.skill_profile import SkillProfile

        with pytest.raises(ValidationError):
            SkillProfile(
                name="test_skill",
                skill_id="",
                skill_set_name="default",
            )

    def test_empty_skill_set_name_raises_error(self):
        """测试空 skill_set_name 抛出错误"""
        from src.domain.models.skill_profile import SkillProfile

        with pytest.raises(ValidationError):
            SkillProfile(
                name="test_skill",
                skill_id="test_v1",
                skill_set_name="",
            )

    def test_extra_fields_forbidden(self):
        """测试额外字段被禁止"""
        from src.domain.models.skill_profile import SkillProfile

        with pytest.raises(ValidationError) as exc_info:
            SkillProfile(
                name="test_skill",
                skill_id="test_v1",
                skill_set_name="default",
                extra_field="not_allowed",  # type: ignore
            )

        assert "extra" in str(exc_info.value).lower()


class TestSkillProfileProperty:
    """测试 SkillProfile 属性方法"""

    def test_display_name_with_description(self):
        """测试带描述的显示名称"""
        from src.domain.models.skill_profile import SkillProfile

        skill = SkillProfile(
            name="web_search",
            description="Search the web",
            skill_id="search_v1",
            skill_set_name="default",
        )

        assert skill.display_name == "web_search: Search the web"

    def test_display_name_without_description(self):
        """测试不带描述的显示名称"""
        from src.domain.models.skill_profile import SkillProfile

        skill = SkillProfile(
            name="web_search",
            skill_id="search_v1",
            skill_set_name="default",
        )

        assert skill.display_name == "web_search"

    def test_searchable_text(self):
        """测试可检索文本"""
        from src.domain.models.skill_profile import SkillProfile

        skill = SkillProfile(
            name="web_search",
            description="Search the web for information",
            skill_id="search_v1",
            skill_set_name="default",
        )

        # searchable_text 应包含 name 和 description
        searchable = skill.searchable_text
        assert "web_search" in searchable
        assert "Search the web for information" in searchable

    def test_searchable_text_no_description(self):
        """测试无描述时可检索文本"""
        from src.domain.models.skill_profile import SkillProfile

        skill = SkillProfile(
            name="simple_skill",
            skill_id="simple_v1",
            skill_set_name="default",
        )

        searchable = skill.searchable_text
        assert "simple_skill" in searchable


class TestSkillProfileDocstring:
    """测试 SkillProfile 文档说明"""

    def test_skill_profile_is_normalized_entry(self):
        """
        SkillProfile 表示归一化后的当前技能条目，
        不是完整的 SkillDefinition/SkillActivation 双层模型。
        """
        from src.domain.models.skill_profile import SkillProfile

        # 这是一个从 skill_sets.json 中 is_current=true 的技能组
        # 提取的已激活技能条目
        skill = SkillProfile(
            name="extracted_skill",
            skill_id="extracted_v1",
            skill_set_name="active_set",
            is_active=True,
        )

        # 验证这是一个简单扁平模型
        assert skill.name
        assert skill.skill_id
        assert skill.skill_set_name
        assert skill.is_active is True