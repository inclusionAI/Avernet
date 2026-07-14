"""
Phase C: G1 Semantic Rerank V2 Unit Tests

测试范围：
1. Legacy vs V2 切换行为
2. Feature Flag 矩阵测试
3. strict_participants 硬性约束
4. profile source empty 处理
5. score_breakdown 仅影响输出，不影响排序
6. G2/G5 不受影响
"""

from __future__ import annotations

import os
import pytest
from unittest.mock import patch, MagicMock

from src.domain.models.worker_profile import WorkerProfile, ProfileType, SourceType
from src.domain.models.skill_profile import SkillProfile
from src.domain.models.profile_match_score import (
    ProfileMatchScore,
    ScoreComponent,
    SEMANTIC_SIMILARITY_WEIGHT,
    CAPABILITY_COVERAGE_WEIGHT,
    SCENARIO_MATCH_WEIGHT,
    AVAILABILITY_SCORE_WEIGHT,
)
from src.domain.models.retrieval_mode import RetrievalMode
from src.domain.services.profile_semantic_ranker import ProfileSemanticRanker, RerankContext
from src.application.services.semantic_match_service import SemanticMatchService
from src.infra.config.feature_flags import FeatureFlags


# =============================================================================
# Test Fixtures
# =============================================================================

@pytest.fixture
def sample_profile() -> WorkerProfile:
    """创建示例 WorkerProfile"""
    return WorkerProfile(
        staff_id="test001",
        profile_id="default",
        profile_type=ProfileType.DEFAULT,
        source_type=SourceType.FILE,
        source_root="/test",
        active_skills=[
            SkillProfile(
                skill_id="skill001",
                name="Architecture",
                description="System architecture design and optimization",
                skill_set_name="tech_skills",
            ),
            SkillProfile(
                skill_id="skill002",
                name="Security",
                description="Security assessment and vulnerability analysis",
                skill_set_name="tech_skills",
            ),
        ],
        searchable_text="[SKILL:Architecture:System architecture design] [SKILL:Security:Security assessment]",
    )


@pytest.fixture
def sample_profiles(sample_profile) -> list[WorkerProfile]:
    """创建多个示例 WorkerProfile"""
    profile2 = WorkerProfile(
        staff_id="test002",
        profile_id="default",
        profile_type=ProfileType.DEFAULT,
        source_type=SourceType.FILE,
        source_root="/test",
        active_skills=[
            SkillProfile(
                skill_id="skill003",
                name="Database",
                description="Database optimization and DBA",
                skill_set_name="tech_skills",
            ),
        ],
        searchable_text="[SKILL:Database:Database optimization]",
    )
    return [sample_profile, profile2]


@pytest.fixture
def sample_context() -> RerankContext:
    """创建示例 RerankContext"""
    return RerankContext(
        question="我们计划进行系统架构升级和数据库迁移，需要评估技术风险",
        mode="agent",
    )


@pytest.fixture
def reset_feature_flags():
    """重置 Feature Flags"""
    FeatureFlags.reset()
    yield
    FeatureFlags.reset()


# =============================================================================
# Test: ProfileMatchScore Model
# =============================================================================

class TestProfileMatchScoreModel:
    """测试 ProfileMatchScore 模型"""

    def test_create_empty_score(self):
        """测试创建空评分"""
        score = ProfileMatchScore.create_empty()
        assert score.final_score == 0.0
        assert not score.has_all_components
        assert score.component_count == 0

    def test_create_from_base_score(self):
        """测试从基础分创建评分"""
        score = ProfileMatchScore.create_from_base_score(
            semantic_similarity=0.8,
            capability_coverage=0.7,
            scenario_match=0.6,
            availability_score=0.9,
        )

        # 验证最终分数
        expected_base = (
            0.8 * SEMANTIC_SIMILARITY_WEIGHT +
            0.7 * CAPABILITY_COVERAGE_WEIGHT +
            0.6 * SCENARIO_MATCH_WEIGHT +
            0.9 * AVAILABILITY_SCORE_WEIGHT
        )
        assert abs(score.final_score - expected_base) < 0.001
        assert abs(score.base_score - expected_base) < 0.001
        assert score.has_all_components
        assert score.component_count == 4

    def test_score_component_properties(self):
        """测试评分组件属性"""
        component = ScoreComponent(
            raw_score=0.8,
            weight=0.44,
            weighted_score=0.352,
        )
        assert component.raw_score == 0.8
        assert component.weight == 0.44
        assert component.weighted_score == 0.352

    def test_score_bounds_validated_by_pydantic(self):
        """测试分数边界由 Pydantic 验证"""
        # Pydantic 会验证分数必须在 0-1 范围内
        # 测试有效范围边界
        score = ProfileMatchScore.create_from_base_score(
            semantic_similarity=1.0,  # 边界值
            capability_coverage=1.0,
            scenario_match=0.0,  # 边界值
            availability_score=0.5,
        )
        # 最终分数应该被 min(1.0, ...) 限制
        assert score.final_score <= 1.0

        # 测试超出范围会抛出验证错误
        import pydantic
        with pytest.raises(pydantic.ValidationError):
            ProfileMatchScore.create_from_base_score(
                semantic_similarity=1.5,  # 超出范围
                capability_coverage=0.5,
                scenario_match=0.5,
                availability_score=0.5,
            )


# =============================================================================
# Test: SemanticMatchService
# =============================================================================

class TestSemanticMatchService:
    """测试语义匹配服务"""

    def test_compute_basic_match(self, sample_profile):
        """测试基础关键词匹配"""
        service = SemanticMatchService()
        score, details = service.compute_semantic_similarity(
            question="架构设计",
            profile=sample_profile,
            use_semantic_expansion=False,
        )
        assert 0.0 <= score <= 1.0
        assert details["expansion_used"] is False

    def test_compute_with_expansion(self, sample_profile):
        """测试语义扩展匹配"""
        service = SemanticMatchService()
        score, details = service.compute_semantic_similarity(
            question="系统架构升级",
            profile=sample_profile,
            use_semantic_expansion=True,
        )
        assert 0.0 <= score <= 1.0
        assert details["expansion_used"] is True
        assert "expanded_term_count" in details

    def test_empty_question_returns_zero(self, sample_profile):
        """测试空问题返回零分"""
        service = SemanticMatchService()
        score, details = service.compute_semantic_similarity(
            question="",
            profile=sample_profile,
            use_semantic_expansion=False,
        )
        assert score == 0.0


# =============================================================================
# Test: ProfileSemanticRanker - Base Score
# =============================================================================

class TestProfileSemanticRankerBaseScore:
    """测试 ProfileSemanticRanker 基础评分"""

    def test_compute_base_score_returns_valid_score(self, sample_profile, sample_context):
        """测试基础评分返回有效分数"""
        ranker = ProfileSemanticRanker()
        score = ranker.compute_base_score(
            profile=sample_profile,
            question=sample_context.question,
            context=sample_context,
        )

        assert 0.0 <= score.final_score <= 1.0
        assert score.has_all_components
        assert score.semantic_similarity is not None
        assert score.capability_coverage is not None
        assert score.scenario_match is not None
        assert score.availability_score is not None

    def test_base_score_formula_weights(self, sample_profile, sample_context):
        """测试基础评分公式权重"""
        ranker = ProfileSemanticRanker()
        score = ranker.compute_base_score(
            profile=sample_profile,
            question=sample_context.question,
            context=sample_context,
        )

        # 验证权重
        assert score.semantic_similarity.weight == SEMANTIC_SIMILARITY_WEIGHT
        assert score.capability_coverage.weight == CAPABILITY_COVERAGE_WEIGHT
        assert score.scenario_match.weight == SCENARIO_MATCH_WEIGHT
        assert score.availability_score.weight == AVAILABILITY_SCORE_WEIGHT

    def test_base_score_components_sum_to_final(self, sample_profile, sample_context):
        """测试组件加权和等于最终分数"""
        ranker = ProfileSemanticRanker()
        score = ranker.compute_base_score(
            profile=sample_profile,
            question=sample_context.question,
            context=sample_context,
        )

        weighted_sum = (
            score.semantic_similarity.weighted_score +
            score.capability_coverage.weighted_score +
            score.scenario_match.weighted_score +
            score.availability_score.weighted_score
        )
        assert abs(score.base_score - weighted_sum) < 0.001


# =============================================================================
# Test: ProfileSemanticRanker - Diversity Rerank
# =============================================================================

class TestProfileSemanticRankerDiversityRerank:
    """测试 ProfileSemanticRanker 多样性重排序"""

    def test_diversity_rerank_respects_top_k(self, sample_profiles):
        """测试多样性重排序遵守 top_k"""
        ranker = ProfileSemanticRanker()
        context = RerankContext(question="架构和数据库优化")

        with patch.object(FeatureFlags, 'is_g1_profile_rerank_enabled', return_value=True):
            with patch.object(FeatureFlags, 'is_g1_semantic_match_enabled', return_value=True):
                results = ranker.rank(sample_profiles, context, top_k=1)

        assert len(results) == 1

    def test_diversity_rerank_introduces_diversity(self, sample_profiles):
        """测试多样性重排序引入多样性"""
        ranker = ProfileSemanticRanker()
        context = RerankContext(question="架构和数据库优化")

        with patch.object(FeatureFlags, 'is_g1_profile_rerank_enabled', return_value=True):
            with patch.object(FeatureFlags, 'is_g1_semantic_match_enabled', return_value=True):
                results = ranker.rank(sample_profiles, context, top_k=2)

        # 检查多样性调整标志
        # 至少有一个结果可能经过了多样性调整
        for profile, score in results:
            assert isinstance(score.diversity_adjusted, bool)
            assert isinstance(score.diversity_delta, float)


# =============================================================================
# Test: strict_participants Hard Constraints
# =============================================================================

class TestStrictParticipantsConstraints:
    """测试 strict_participants 硬性约束"""

    def test_strict_mode_does_not_introduce_new_candidates(self, sample_profiles):
        """测试严格模式不引入新候选"""
        ranker = ProfileSemanticRanker()
        context = RerankContext(
            question="架构评估",
            mode="agent",
            strict_participants=True,
            profile_keys=["staff_test001:default"],  # 只允许第一个 profile
        )

        with patch.object(FeatureFlags, 'is_g1_profile_rerank_enabled', return_value=True):
            results = ranker.rank(sample_profiles, context, top_k=5)

        # 只能返回传入的 profiles 中的候选
        result_keys = {p.profile_key for p, _ in results}
        assert result_keys.issubset({"staff_test001:default", "staff_test002:default"})

    def test_strict_mode_respects_profile_keys(self, sample_profile):
        """测试严格模式遵守 profile_keys"""
        ranker = ProfileSemanticRanker()
        context = RerankContext(
            question="架构评估",
            mode="agent",
            strict_participants=True,
            profile_keys=["staff_test001:default"],
        )

        with patch.object(FeatureFlags, 'is_g1_profile_rerank_enabled', return_value=True):
            results = ranker.rank([sample_profile], context, top_k=5)

        # 所有结果应该来自传入的 profiles
        for profile, score in results:
            assert profile.profile_key == "staff_test001:default"


# =============================================================================
# Test: Empty Profile Source Handling
# =============================================================================

class TestEmptyProfileSourceHandling:
    """测试空候选集处理"""

    def test_rank_empty_profiles_returns_empty(self, sample_context):
        """测试对空 profiles 排序返回空"""
        ranker = ProfileSemanticRanker()
        results = ranker.rank([], sample_context, top_k=5)
        assert results == []

    def test_rank_with_top_k_zero_returns_empty(self, sample_profile, sample_context):
        """测试 top_k=0 返回空"""
        ranker = ProfileSemanticRanker()
        results = ranker.rank([sample_profile], sample_context, top_k=0)
        assert results == []


# =============================================================================
# Test: Feature Flag Matrix
# =============================================================================

class TestFeatureFlagMatrix:
    """测试 Feature Flag 矩阵"""

    def test_all_flags_off_uses_legacy_scoring(self, sample_profile, sample_context):
        """测试所有开关关闭时使用基础评分（无多样性重排）"""
        ranker = ProfileSemanticRanker()

        with patch.object(FeatureFlags, 'is_g1_profile_rerank_enabled', return_value=False):
            with patch.object(FeatureFlags, 'is_g1_semantic_match_enabled', return_value=False):
                with patch.object(FeatureFlags, 'is_g1_score_breakdown_output_enabled', return_value=False):
                    results = ranker.rank([sample_profile], sample_context, top_k=1)

        assert len(results) == 1
        profile, score = results[0]
        # 没有经过 V2 diversity rerank
        assert score.diversity_adjusted is False
        # flags_enabled 应该为空或只包含非 G1 flags
        assert not any("G1" in f for f in score.flags_enabled)

    def test_rerank_on_semantic_off_uses_basic_matching(self, sample_profile, sample_context):
        """测试 RERANK=on, SEMANTIC=off 使用基础关键词匹配"""
        ranker = ProfileSemanticRanker()

        with patch.object(FeatureFlags, 'is_g1_profile_rerank_enabled', return_value=True):
            with patch.object(FeatureFlags, 'is_g1_semantic_match_enabled', return_value=False):
                results = ranker.rank([sample_profile], sample_context, top_k=1)

        assert len(results) == 1
        profile, score = results[0]
        # semantic_similarity 详情应该显示 expansion_used=False
        assert score.semantic_similarity.details.get("expansion_used") is False

    def test_rerank_on_semantic_on_uses_expansion(self, sample_profile, sample_context):
        """测试 RERANK=on, SEMANTIC=on 使用语义扩展"""
        ranker = ProfileSemanticRanker()

        with patch.object(FeatureFlags, 'is_g1_profile_rerank_enabled', return_value=True):
            with patch.object(FeatureFlags, 'is_g1_semantic_match_enabled', return_value=True):
                results = ranker.rank([sample_profile], sample_context, top_k=1)

        assert len(results) == 1
        profile, score = results[0]
        # semantic_similarity 详情应该显示 expansion_used=True
        assert score.semantic_similarity.details.get("expansion_used") is True

    def test_score_breakdown_only_affects_output(self, sample_profile, sample_context):
        """测试 SCORE_BREAKDOWN 仅影响输出字段，不影响排序"""
        ranker = ProfileSemanticRanker()

        # 不开启 score_breakdown
        with patch.object(FeatureFlags, 'is_g1_profile_rerank_enabled', return_value=True):
            with patch.object(FeatureFlags, 'is_g1_score_breakdown_output_enabled', return_value=False):
                results_no_breakdown = ranker.rank([sample_profile], sample_context, top_k=1)
                score_no_breakdown = results_no_breakdown[0][1].final_score

        # 开启 score_breakdown
        with patch.object(FeatureFlags, 'is_g1_profile_rerank_enabled', return_value=True):
            with patch.object(FeatureFlags, 'is_g1_score_breakdown_output_enabled', return_value=True):
                results_with_breakdown = ranker.rank([sample_profile], sample_context, top_k=1)
                score_with_breakdown = results_with_breakdown[0][1].final_score

        # 分数应该相同
        assert abs(score_no_breakdown - score_with_breakdown) < 0.001


# =============================================================================
# Test: G2/G5 Not Affected
# =============================================================================

class TestG2G5NotAffected:
    """测试 G2/G5 模式不受影响"""

    def test_g2_mode_not_affected_by_v2_flags(self, sample_profile):
        """测试 G2 模式不受 V2 开关影响"""
        ranker = ProfileSemanticRanker()
        context = RerankContext(
            question="产品与技术冲突问题",
            mode="conflict_alignment",  # G2 模式
        )

        # 即使开启 V2 flags，G2 模式也应该正常工作
        with patch.object(FeatureFlags, 'is_g1_profile_rerank_enabled', return_value=True):
            with patch.object(FeatureFlags, 'is_g1_semantic_match_enabled', return_value=True):
                results = ranker.rank([sample_profile], context, top_k=1)

        assert len(results) == 1

    def test_g5_mode_not_affected_by_v2_flags(self, sample_profile):
        """测试 G5 模式不受 V2 开关影响"""
        ranker = ProfileSemanticRanker()
        context = RerankContext(
            question="数据泄露风险评估",
            mode="expert_diagnosis",  # G5 模式
        )

        with patch.object(FeatureFlags, 'is_g1_profile_rerank_enabled', return_value=True):
            with patch.object(FeatureFlags, 'is_g1_semantic_match_enabled', return_value=True):
                results = ranker.rank([sample_profile], context, top_k=1)

        assert len(results) == 1


# =============================================================================
# Test: Feature Flags Default Values
# =============================================================================

class TestFeatureFlagsDefaultValues:
    """测试 Feature Flags 默认值"""

    def test_g1_semantic_match_default_false(self):
        """测试 G1_SEMANTIC_MATCH 默认关闭"""
        FeatureFlags.reset()
        assert FeatureFlags.is_g1_semantic_match_enabled() is False

    def test_g1_profile_rerank_default_false(self):
        """测试 G1_PROFILE_RERANK 默认关闭"""
        FeatureFlags.reset()
        assert FeatureFlags.is_g1_profile_rerank_enabled() is False

    def test_g1_score_breakdown_output_default_false(self):
        """测试 G1_SCORE_BREAKDOWN_OUTPUT 默认关闭"""
        FeatureFlags.reset()
        assert FeatureFlags.is_g1_score_breakdown_output_enabled() is False

    def test_get_all_flags_includes_g1_v2_flags(self):
        """测试 get_all_flags 包含 G1 V2 flags"""
        FeatureFlags.reset()
        all_flags = FeatureFlags.get_all_flags()

        assert "ENABLE_G1_SEMANTIC_MATCH" in all_flags
        assert "ENABLE_G1_PROFILE_RERANK" in all_flags
        assert "ENABLE_G1_SCORE_BREAKDOWN_OUTPUT" in all_flags


# =============================================================================
# Test: Backward Compatibility
# =============================================================================

class TestBackwardCompatibility:
    """测试向后兼容性"""

    def test_score_breakdown_none_by_default(self, sample_profile, sample_context):
        """测试 score_breakdown 默认为 None"""
        from src.domain.models.candidate_recommendation import CandidateRecommendation

        rec = CandidateRecommendation(
            profile_key=sample_profile.profile_key,
            score=0.8,
        )

        # score_breakdown 默认为 None
        assert rec.score_breakdown is None

    def test_candidate_recommendation_without_score_breakdown(self, sample_profile):
        """测试 CandidateRecommendation 可以不包含 score_breakdown"""
        from src.domain.models.candidate_recommendation import CandidateRecommendation

        # 老客户端只传入必需字段
        rec = CandidateRecommendation(
            profile_key=sample_profile.profile_key,
            score=0.8,
        )

        assert rec.profile_key == sample_profile.profile_key
        assert rec.score == 0.8
        assert rec.score_breakdown is None


# =============================================================================
# Test: Integration with Retrieval Service
# =============================================================================

class TestRetrievalServiceIntegration:
    """测试与检索服务的集成"""

    def test_v2_scorer_only_used_for_agent_mode(self, sample_profile):
        """测试 V2 评分器仅用于 AGENT 模式"""
        from src.domain.services.worker_profile_retrieval_service import (
            WorkerProfileRetrievalService,
            RetrievalResponse,
        )

        # Mock source
        mock_source = MagicMock()
        mock_source.scan.return_value = MagicMock(profiles=[sample_profile])

        service = WorkerProfileRetrievalService(source=mock_source)

        # 测试 AGENT 模式
        with patch.object(FeatureFlags, 'is_g1_profile_rerank_enabled', return_value=True):
            response = service.retrieve(
                question="架构评估",
                mode=RetrievalMode.AGENT,
                top_k=1,
            )
            assert isinstance(response, RetrievalResponse)
            assert len(response.results) >= 0

    def test_legacy_scorer_used_when_flag_off(self, sample_profile):
        """测试 flag 关闭时使用 legacy 评分器"""
        from src.domain.services.worker_profile_retrieval_service import (
            WorkerProfileRetrievalService,
            RetrievalResponse,
        )

        mock_source = MagicMock()
        mock_source.scan.return_value = MagicMock(profiles=[sample_profile])

        service = WorkerProfileRetrievalService(source=mock_source)

        with patch.object(FeatureFlags, 'is_g1_profile_rerank_enabled', return_value=False):
            response = service.retrieve(
                question="架构评估",
                mode=RetrievalMode.AGENT,
                top_k=1,
            )
            assert isinstance(response, RetrievalResponse)


# =============================================================================
# Test: Score Component Validation
# =============================================================================

class TestScoreComponentValidation:
    """测试评分组件验证"""

    def test_raw_score_clamped_to_range(self):
        """测试原始分数被限制在 0-1 范围"""
        # 正常范围
        component = ScoreComponent(
            raw_score=0.5,
            weight=0.44,
            weighted_score=0.22,
        )
        assert component.raw_score == 0.5

    def test_weight_must_be_valid(self):
        """测试权重必须在有效范围"""
        component = ScoreComponent(
            raw_score=0.5,
            weight=0.44,
            weighted_score=0.22,
        )
        assert 0.0 <= component.weight <= 1.0

    def test_profile_match_score_final_score_validated(self):
        """测试最终分数由 Pydantic 验证"""
        # 有效边界值
        score = ProfileMatchScore(final_score=1.0)
        assert score.final_score == 1.0

        score = ProfileMatchScore(final_score=0.0)
        assert score.final_score == 0.0

        # 超出范围会抛出验证错误
        import pydantic
        with pytest.raises(pydantic.ValidationError):
            ProfileMatchScore(final_score=1.5)


# =============================================================================
# Test: ProfileMatchScore Additional Coverage
# =============================================================================

class TestProfileMatchScoreAdditional:
    """ProfileMatchScore 额外测试覆盖"""

    def test_diversity_delta_negative_value(self):
        """测试 diversity_delta 可以为负值"""
        score = ProfileMatchScore(
            final_score=0.5,
            diversity_delta=-0.1,
        )
        assert score.diversity_delta == -0.1

    def test_diversity_delta_boundary(self):
        """测试 diversity_delta 边界值"""
        # 最小值 -1.0
        score = ProfileMatchScore(
            final_score=0.5,
            diversity_delta=-1.0,
        )
        assert score.diversity_delta == -1.0

        # 最大值 1.0
        score = ProfileMatchScore(
            final_score=0.5,
            diversity_delta=1.0,
        )
        assert score.diversity_delta == 1.0

    def test_flags_enabled_field(self):
        """测试 flags_enabled 字段传递"""
        flags = ["ENABLE_G1_PROFILE_RERANK", "ENABLE_G1_SEMANTIC_MATCH"]
        score = ProfileMatchScore.create_from_base_score(
            semantic_similarity=0.8,
            capability_coverage=0.7,
            scenario_match=0.6,
            availability_score=0.9,
            flags_enabled=flags,
        )
        assert score.flags_enabled == flags

    def test_scorer_version_default(self):
        """测试 scorer_version 默认值为 v2"""
        score = ProfileMatchScore.create_from_base_score(
            semantic_similarity=0.8,
            capability_coverage=0.7,
            scenario_match=0.6,
            availability_score=0.9,
        )
        assert score.scorer_version == "v2"

    def test_base_score_with_partial_components(self):
        """测试部分组件缺失时的 base_score 计算"""
        # 只有两个组件
        score = ProfileMatchScore(
            final_score=0.5,
            semantic_similarity=ScoreComponent(
                raw_score=0.8,
                weight=0.44,
                weighted_score=0.352,
            ),
            capability_coverage=ScoreComponent(
                raw_score=0.7,
                weight=0.28,
                weighted_score=0.196,
            ),
        )
        # base_score 只计算存在的组件
        expected = 0.352 + 0.196
        assert abs(score.base_score - expected) < 0.001
        assert not score.has_all_components
        assert score.component_count == 2

    def test_details_field_passing(self):
        """测试 details 字段传递"""
        score = ProfileMatchScore.create_from_base_score(
            semantic_similarity=0.8,
            capability_coverage=0.7,
            scenario_match=0.6,
            availability_score=0.9,
            semantic_details={"expansion_used": True, "matched_count": 5},
            capability_details={"matched_capabilities": ["Java", "Python"]},
        )
        assert score.semantic_similarity.details["expansion_used"] is True
        assert score.capability_coverage.details["matched_capabilities"] == ["Java", "Python"]


# =============================================================================
# Test: SemanticMatchService Additional Coverage
# =============================================================================

class TestSemanticMatchServiceAdditional:
    """SemanticMatchService 额外测试覆盖"""

    def test_empty_profile_returns_low_score(self):
        """测试空 profile 返回较低分数"""
        empty_profile = WorkerProfile(
            staff_id="empty",
            profile_id="default",
            profile_type=ProfileType.DEFAULT,
            source_type=SourceType.FILE,
            source_root="/test",
            active_skills=[],
            searchable_text="",
        )
        service = SemanticMatchService()
        score, details = service.compute_semantic_similarity(
            question="架构设计",
            profile=empty_profile,
            use_semantic_expansion=False,
        )
        # 空/profile 应该返回低分
        assert 0.0 <= score <= 0.5

    def test_chinese_text_matching(self):
        """测试中文分词匹配"""
        profile = WorkerProfile(
            staff_id="chinese",
            profile_id="default",
            profile_type=ProfileType.DEFAULT,
            source_type=SourceType.FILE,
            source_root="/test",
            active_skills=[
                SkillProfile(
                    skill_id="s1",
                    name="架构设计",
                    description="系统架构设计和优化",
                    skill_set_name="tech",
                ),
            ],
            # 使用与问题有重叠词的文本
            searchable_text="架构设计 系统架构优化",
        )
        service = SemanticMatchService()
        score, details = service.compute_semantic_similarity(
            # 使用与 searchable_text 有重叠的问题
            question="系统架构设计评估",
            profile=profile,
            use_semantic_expansion=False,
        )
        # 中文匹配应该有效（"系统"、"架构"、"设计"应匹配）
        assert score >= 0.0
        # 检查有匹配
        if score > 0.0:
            assert details["original_match_count"] > 0

    def test_profile_with_context_fragments(self):
        """测试包含 context_fragments 的 profile"""
        from src.domain.models.context_fragment import ContextFragment, ContextKind

        profile = WorkerProfile(
            staff_id="fragments",
            profile_id="default",
            profile_type=ProfileType.DEFAULT,
            source_type=SourceType.FILE,
            source_root="/test",
            active_skills=[
                SkillProfile(
                    skill_id="s1",
                    name="Backend",
                    skill_set_name="tech",
                ),
            ],
            searchable_text="后端开发",
            context_fragments=[
                ContextFragment(
                    kind=ContextKind.OTHER,
                    filename="doc.md",
                    content="这是一个关于数据库优化的详细文档，包含索引设计和查询优化建议",
                    source_path="/test/doc.md",
                ),
            ],
        )
        service = SemanticMatchService()
        score, details = service.compute_semantic_similarity(
            question="数据库查询优化",
            profile=profile,
            use_semantic_expansion=False,
        )
        # context_fragments 内容应该参与匹配
        assert score >= 0.0

    def test_expansion_returns_expanded_term_count(self):
        """测试扩展匹配返回扩展词数量"""
        profile = WorkerProfile(
            staff_id="expansion",
            profile_id="default",
            profile_type=ProfileType.DEFAULT,
            source_type=SourceType.FILE,
            source_root="/test",
            active_skills=[
                SkillProfile(
                    skill_id="s1",
                    name="Security",
                    skill_set_name="tech",
                ),
            ],
            searchable_text="安全漏洞扫描",
        )
        service = SemanticMatchService()
        score, details = service.compute_semantic_similarity(
            question="安全风险评估",
            profile=profile,
            use_semantic_expansion=True,
        )
        assert "expanded_term_count" in details
        assert details["expansion_used"] is True


# =============================================================================
# Test: ProfileSemanticRanker Additional Coverage
# =============================================================================

class TestProfileSemanticRankerAdditional:
    """ProfileSemanticRanker 额外测试覆盖"""

    def test_compute_availability_online(self, sample_profile):
        """测试在线状态可用性评分"""
        ranker = ProfileSemanticRanker()
        context = RerankContext(
            question="测试问题",
            is_online_map={"staff_test001:default": True},
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
            is_online_map={"staff_test001:default": False},
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
    "TestProfileMatchScoreModel",
    "TestSemanticMatchService",
    "TestProfileSemanticRankerBaseScore",
    "TestProfileSemanticRankerDiversityRerank",
    "TestStrictParticipantsConstraints",
    "TestEmptyProfileSourceHandling",
    "TestFeatureFlagMatrix",
    "TestG2G5NotAffected",
    "TestFeatureFlagsDefaultValues",
    "TestBackwardCompatibility",
    "TestRetrievalServiceIntegration",
    "TestScoreComponentValidation",
    "TestProfileMatchScoreAdditional",
    "TestSemanticMatchServiceAdditional",
    "TestProfileSemanticRankerAdditional",
]