"""Semantic ranking availability, capability, and ordering coverage."""

from __future__ import annotations

from unittest.mock import patch

from src.domain.models.worker_profile import WorkerProfile, ProfileType, SourceType
from src.domain.models.skill_profile import SkillProfile
from src.domain.services.profile_semantic_ranker import ProfileSemanticRanker, RerankContext
from src.infra.config.feature_flags import FeatureFlags


from tests.unit.domain.services.test_profile_semantic_ranker import sample_profile as sample_profile


class TestProfileSemanticRankerAdditional:
    """ProfileSemanticRanker 额外测试覆盖"""

    def test_compute_availability_online(self, sample_profile):
        """测试在线状态可用性评分"""
        ranker = ProfileSemanticRanker()
        context = RerankContext(
            question="测试问题",
            is_online_map={"test001:default": True},
        )
        score = ranker.compute_base_score(
            profile=sample_profile,
            question="测试问题",
            context=context,
        )
        # 在线状态应该获得满分
        assert score.availability_score.raw_score == 1.0
        assert score.availability_score.details["status"] == "online"

    def test_compute_availability_offline(self, sample_profile):
        """测试离线状态可用性评分"""
        ranker = ProfileSemanticRanker()
        context = RerankContext(
            question="测试问题",
            is_online_map={"test001:default": False},
        )
        score = ranker.compute_base_score(
            profile=sample_profile,
            question="测试问题",
            context=context,
        )
        # 离线状态应该获得较低分
        assert score.availability_score.raw_score == 0.5
        assert score.availability_score.details["status"] == "offline"

    def test_compute_availability_no_info(self, sample_profile):
        """测试无可用性信息时的默认评分"""
        ranker = ProfileSemanticRanker()
        context = RerankContext(
            question="测试问题",
            is_online_map=None,
        )
        score = ranker.compute_base_score(
            profile=sample_profile,
            question="测试问题",
            context=context,
        )
        # 无信息时默认可用
        assert score.availability_score.raw_score == 1.0
        assert "no availability info" in score.availability_score.details.get("reason", "")

    def test_compute_capability_coverage_no_keywords(self):
        """测试无能力关键词提取时的默认评分"""
        profile = WorkerProfile(
            staff_id="no_kw",
            profile_id="default",
            profile_type=ProfileType.DEFAULT,
            source_type=SourceType.FILE,
            source_root="/test",
            active_skills=[
                SkillProfile(
                    skill_id="s1",
                    name="General",
                    skill_set_name="general",
                ),
            ],
            searchable_text="通用技能",
        )
        ranker = ProfileSemanticRanker()
        context = RerankContext(question="这是一个普通问题")  # 不包含任何领域关键词
        score = ranker.compute_base_score(
            profile=profile,
            question="这是一个普通问题",
            context=context,
        )
        # 无关键词时应该有默认值
        assert 0.0 <= score.capability_coverage.raw_score <= 1.0

    def test_compute_scenario_match_no_match(self):
        """测试无场景匹配时的默认评分"""
        profile = WorkerProfile(
            staff_id="no_scenario",
            profile_id="default",
            profile_type=ProfileType.DEFAULT,
            source_type=SourceType.FILE,
            source_root="/test",
            active_skills=[
                SkillProfile(
                    skill_id="s1",
                    name="General",
                    skill_set_name="general",
                ),
            ],
            searchable_text="通用技能",
        )
        ranker = ProfileSemanticRanker()
        context = RerankContext(question="今天天气怎么样")  # 不匹配任何业务场景
        score = ranker.compute_base_score(
            profile=profile,
            question="今天天气怎么样",
            context=context,
        )
        # 无场景匹配时应该有默认值
        assert 0.0 <= score.scenario_match.raw_score <= 1.0

    def test_rank_multiple_profiles_ordering(self):
        """测试多 profile 排序正确性"""
        # 创建不同相关性的 profiles
        high_rel_profile = WorkerProfile(
            staff_id="high",
            profile_id="default",
            profile_type=ProfileType.DEFAULT,
            source_type=SourceType.FILE,
            source_root="/test",
            active_skills=[
                SkillProfile(
                    skill_id="s1",
                    name="Architecture",
                    description="System architecture design",
                    skill_set_name="tech",
                ),
            ],
            searchable_text="系统架构设计 架构优化",
        )
        low_rel_profile = WorkerProfile(
            staff_id="low",
            profile_id="default",
            profile_type=ProfileType.DEFAULT,
            source_type=SourceType.FILE,
            source_root="/test",
            active_skills=[
                SkillProfile(
                    skill_id="s2",
                    name="General",
                    skill_set_name="general",
                ),
            ],
            searchable_text="通用技能",
        )
        ranker = ProfileSemanticRanker()
        context = RerankContext(question="系统架构升级评估")

        with patch.object(FeatureFlags, 'is_g1_profile_rerank_enabled', return_value=True):
            with patch.object(FeatureFlags, 'is_g1_semantic_match_enabled', return_value=True):
                results = ranker.rank(
                    [low_rel_profile, high_rel_profile],
                    context,
                    top_k=2,
                )

        # 高相关性应该排在前面
        assert len(results) == 2
        assert results[0][0].staff_id == "high"


__all__ = [
    "TestProfileSemanticRankerAdditional",
]
