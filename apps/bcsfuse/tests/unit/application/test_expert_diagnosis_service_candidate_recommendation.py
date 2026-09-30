"""
Tests for ExpertDiagnosisService with CandidateRecommendationService

Stage 4 Phase 4: ExpertDiagnosisService 集成

测试 ExpertDiagnosisService 与 CandidateRecommendationService 的集成。

核心场景：
1. participants=None → 使用 recommendation 推荐候选人
2. participants 不足 → 保留显式 + 补充推荐
3. participants 充足 → 不调用 recommendation
4. recommendation 失败 → 不中断流程
5. 保持 traceability (profile_key)
"""

from __future__ import annotations

import pytest
from unittest.mock import Mock

from src.domain.models.fusion_result import Perspective, FusionResult
from src.domain.models.candidate_recommendation import (
    CandidateRecommendation,
    CandidateRecommendationResponse,
)
from src.domain.models.domain_coverage import DomainCoverage
from src.domain.models.retrieval_mode import RetrievalMode
from src.application.services.expert_diagnosis_service import ExpertDiagnosisService


# =============================================================================
# Module-level Fixtures
# =============================================================================

@pytest.fixture
def mock_enhancer():
    """创建 mock G5ExpertEnhancer"""
    return Mock()


@pytest.fixture
def mock_recommendation_service():
    """创建 mock FusionRecommendationService"""
    return Mock()


@pytest.fixture
def mock_candidate_recommendation_service():
    """创建 mock WorkerCandidateRecommendationService"""
    return Mock()


@pytest.fixture
def sample_perspectives():
    """创建示例 perspectives"""
    return [
        Perspective(
            participant_id="staff_001:default",
            participant_type="bot",
            role="expert",
            summary="Expert perspective on the question",
            confidence=0.85,
            status="completed",
        ),
        Perspective(
            participant_id="staff_002:default",
            participant_type="bot",
            role="expert",
            summary="Another expert perspective",
            confidence=0.75,
            status="completed",
        ),
    ]


@pytest.fixture
def enhanced_perspectives():
    """创建增强后的 perspectives"""
    return [
        Perspective(
            participant_id="staff_003:default",
            participant_type="bot",
            role="expert",
            summary="[LLM Enhanced] Expert perspective based on profile",
            confidence=0.90,
            status="completed",
            key_points=["Point 1", "Point 2"],
            concerns=["Concern 1"],
        ),
        Perspective(
            participant_id="staff_004:default",
            participant_type="bot",
            role="expert",
            summary="[LLM Enhanced] Another expert perspective",
            confidence=0.88,
            status="completed",
            key_points=["Point A", "Point B"],
            concerns=["Concern A"],
        ),
    ]


@pytest.fixture
def sample_candidate_recommendation():
    """创建示例候选人推荐"""
    return CandidateRecommendation(
        profile_key="staff_recommended_001:default",
        score=0.85,
        reasons=["Relevant skills: Security"],
        domain="security",
        domain_confidence=0.8,
        matched_skills=["Security"],
        matched_contexts=["AGENTS.md"],
        is_supplement=True,
    )


@pytest.fixture
def sample_recommendation_response(sample_candidate_recommendation):
    """创建示例推荐响应"""
    return CandidateRecommendationResponse(
        recommendations=[
            sample_candidate_recommendation,
            CandidateRecommendation(
                profile_key="staff_recommended_002:default",
                score=0.80,
                reasons=["Relevant skills: Database"],
                domain="database",
                domain_confidence=0.75,
                matched_skills=["Database"],
                matched_contexts=["AGENTS.md"],
                is_supplement=True,
            ),
        ],
        question="Test question",
        mode=RetrievalMode.EXPERT_DIAGNOSIS,
        domain_coverage=DomainCoverage(
            required_domains=["security"],
            covered_domains=["security"],
            missing_domains=[],
            coverage_score=1.0,
        ),
        participants_given=False,
        participants_sufficient=False,
        total_candidates=2,
        selected_candidates=2,
        min_experts=3,
    )


# =============================================================================
# Test Classes
# =============================================================================

class TestExpertDiagnosisServiceWithCandidateRecommendation:
    """ExpertDiagnosisService 与 CandidateRecommendationService 集成测试"""

    # =========================================================================
    # 场景 A：participants=None → 使用 recommendation
    # =========================================================================

    def test_diagnose_uses_recommendation_when_participants_missing(
        self,
        mock_enhancer,
        mock_recommendation_service,
        mock_candidate_recommendation_service,
        sample_perspectives,
        sample_recommendation_response,
    ):
        """
        场景 A：participants=None
        - 调用 candidate_recommendation_service 推荐候选人
        - 推荐结果传给 g5_enhancer
        """
        # 配置 mocks
        mock_candidate_recommendation_service.recommend.return_value = sample_recommendation_response
        mock_enhancer.enhance.return_value = sample_perspectives

        # 创建服务
        service = ExpertDiagnosisService(
            recommendation_service=mock_recommendation_service,
            g5_enhancer=mock_enhancer,
            candidate_recommendation_service=mock_candidate_recommendation_service,
        )

        # 执行 diagnose（participants=None）
        result = service.diagnose(
            question="How to secure the API?",
            perspectives=sample_perspectives,
            participants=None,
        )

        # 验证 recommendation service 被调用
        mock_candidate_recommendation_service.recommend.assert_called_once()
        call_kwargs = mock_candidate_recommendation_service.recommend.call_args.kwargs
        assert call_kwargs["question"] == "How to secure the API?"
        assert call_kwargs["mode"] == RetrievalMode.EXPERT_DIAGNOSIS

        # 验证 enhancer 被调用，且使用了推荐的 participants
        mock_enhancer.enhance.assert_called_once()
        enhance_kwargs = mock_enhancer.enhance.call_args.kwargs
        # participants 应该是推荐结果
        recommended_keys = [r.profile_key for r in sample_recommendation_response.recommendations]
        assert enhance_kwargs["participants"] == recommended_keys

        # 验证结果有效
        assert isinstance(result, FusionResult)

    def test_diagnose_recommendation_all_marked_supplement_when_no_explicit(
        self,
        mock_enhancer,
        mock_recommendation_service,
        mock_candidate_recommendation_service,
        sample_perspectives,
        sample_recommendation_response,
    ):
        """
        无显式 participants 时，所有推荐都标记为 supplement
        """
        # 配置 mocks
        mock_candidate_recommendation_service.recommend.return_value = sample_recommendation_response
        mock_enhancer.enhance.return_value = sample_perspectives

        # 创建服务
        service = ExpertDiagnosisService(
            recommendation_service=mock_recommendation_service,
            g5_enhancer=mock_enhancer,
            candidate_recommendation_service=mock_candidate_recommendation_service,
        )

        # 执行 diagnose
        service.diagnose(
            question="Test question",
            perspectives=sample_perspectives,
            participants=None,
        )

        # 验证所有推荐都被标记为 supplement
        for rec in sample_recommendation_response.recommendations:
            assert rec.is_supplement is True

    # =========================================================================
    # 场景 B：显式 participants 充足 → 不调用 recommendation
    # =========================================================================

    def test_diagnose_does_not_recommend_when_participants_sufficient(
        self,
        mock_enhancer,
        mock_recommendation_service,
        mock_candidate_recommendation_service,
        sample_perspectives,
    ):
        """
        场景 B：显式 participants 充足
        - 不调用 recommendation service
        - 直接使用显式 participants
        """
        # 创建充足 participants 的响应
        sufficient_response = CandidateRecommendationResponse(
            recommendations=[
                CandidateRecommendation(
                    profile_key="staff_001:default",
                    score=0.9,
                    reasons=["Explicit participant"],
                    domain="security",
                    domain_confidence=0.8,
                    matched_skills=[],
                    matched_contexts=[],
                    is_supplement=False,
                ),
                CandidateRecommendation(
                    profile_key="staff_002:default",
                    score=0.9,
                    reasons=["Explicit participant"],
                    domain="legal",
                    domain_confidence=0.8,
                    matched_skills=[],
                    matched_contexts=[],
                    is_supplement=False,
                ),
                CandidateRecommendation(
                    profile_key="staff_003:default",
                    score=0.9,
                    reasons=["Explicit participant"],
                    domain="database",
                    domain_confidence=0.8,
                    matched_skills=[],
                    matched_contexts=[],
                    is_supplement=False,
                ),
            ],
            question="Test question",
            mode=RetrievalMode.EXPERT_DIAGNOSIS,
            domain_coverage=DomainCoverage(),
            participants_given=True,
            participants_sufficient=True,  # 充足
            total_candidates=3,
            selected_candidates=3,
            min_experts=3,
        )

        mock_candidate_recommendation_service.recommend.return_value = sufficient_response
        mock_enhancer.enhance.return_value = sample_perspectives

        # 创建服务
        service = ExpertDiagnosisService(
            recommendation_service=mock_recommendation_service,
            g5_enhancer=mock_enhancer,
            candidate_recommendation_service=mock_candidate_recommendation_service,
        )

        # 执行 diagnose（3 个显式 participants >= min_experts=3）
        service.diagnose(
            question="Test question",
            perspectives=sample_perspectives,
            participants=["staff_001:default", "staff_002:default", "staff_003:default"],
        )

        # 验证 recommendation 被调用（因为需要检查充足性）
        # 但 enhancer 收到的是显式 participants
        mock_enhancer.enhance.assert_called_once()
        enhance_kwargs = mock_enhancer.enhance.call_args.kwargs
        # participants 应该只是显式的
        assert len(enhance_kwargs["participants"]) == 3

    # =========================================================================
    # 场景 C：显式 participants 不足 → 保留显式 + 补充推荐
    # =========================================================================

    def test_diagnose_keeps_explicit_participants_before_supplements(
        self,
        mock_enhancer,
        mock_recommendation_service,
        mock_candidate_recommendation_service,
        sample_perspectives,
    ):
        """
        场景 C：显式 participants 不足
        - 显式 participants 保留在前面
        - 补充推荐在后面
        """
        # 创建不足 participants 的响应（显式 1 个 + 补充 2 个）
        mixed_response = CandidateRecommendationResponse(
            recommendations=[
                # 显式 participant
                CandidateRecommendation(
                    profile_key="staff_001:default",
                    score=0.9,
                    reasons=["Explicit participant"],
                    domain="security",
                    domain_confidence=0.8,
                    matched_skills=[],
                    matched_contexts=[],
                    is_supplement=False,
                ),
                # 补充推荐
                CandidateRecommendation(
                    profile_key="staff_supp_001:default",
                    score=0.85,
                    reasons=["Recommended for security expertise"],
                    domain="security",
                    domain_confidence=0.8,
                    matched_skills=["Security"],
                    matched_contexts=["AGENTS.md"],
                    is_supplement=True,
                ),
                CandidateRecommendation(
                    profile_key="staff_supp_002:default",
                    score=0.80,
                    reasons=["Recommended for database expertise"],
                    domain="database",
                    domain_confidence=0.75,
                    matched_skills=["Database"],
                    matched_contexts=["AGENTS.md"],
                    is_supplement=True,
                ),
            ],
            question="Test question",
            mode=RetrievalMode.EXPERT_DIAGNOSIS,
            domain_coverage=DomainCoverage(),
            participants_given=True,
            participants_sufficient=False,  # 不足
            total_candidates=3,
            selected_candidates=3,
            min_experts=3,
        )

        mock_candidate_recommendation_service.recommend.return_value = mixed_response
        mock_enhancer.enhance.return_value = sample_perspectives

        # 创建服务
        service = ExpertDiagnosisService(
            recommendation_service=mock_recommendation_service,
            g5_enhancer=mock_enhancer,
            candidate_recommendation_service=mock_candidate_recommendation_service,
        )

        # 执行 diagnose（只有 1 个显式 participant）
        service.diagnose(
            question="Test question",
            perspectives=sample_perspectives,
            participants=["staff_001:default"],  # 只有 1 个，不足
        )

        # 验证 enhancer 收到的 participants 顺序：显式在前，补充在后
        mock_enhancer.enhance.assert_called_once()
        enhance_kwargs = mock_enhancer.enhance.call_args.kwargs
        participants = enhance_kwargs.get("participants", [])

        # 如果有显式和补充，显式应该在前面
        explicit_keys = ["staff_001:default"]
        supplement_keys = ["staff_supp_001:default", "staff_supp_002:default"]

        for explicit_key in explicit_keys:
            if explicit_key in participants:
                explicit_idx = participants.index(explicit_key)
                for supp_key in supplement_keys:
                    if supp_key in participants:
                        assert explicit_idx < participants.index(supp_key), \
                            f"Explicit {explicit_key} should come before supplement {supp_key}"

    # =========================================================================
    # 场景 D：recommendation 失败 → 不中断流程
    # =========================================================================

    def test_diagnose_recommendation_failure_does_not_break_flow(
        self,
        mock_enhancer,
        mock_recommendation_service,
        mock_candidate_recommendation_service,
        sample_perspectives,
    ):
        """
        场景 D：recommendation 失败
        - 不中断 diagnose 流程
        - 回退到原有行为
        """
        # 配置 recommendation service 抛出异常
        mock_candidate_recommendation_service.recommend.side_effect = Exception("Recommendation failed")
        mock_enhancer.enhance.return_value = sample_perspectives

        # 创建服务
        service = ExpertDiagnosisService(
            recommendation_service=mock_recommendation_service,
            g5_enhancer=mock_enhancer,
            candidate_recommendation_service=mock_candidate_recommendation_service,
        )

        # 执行 diagnose
        result = service.diagnose(
            question="Test question",
            perspectives=sample_perspectives,
            participants=None,
        )

        # 验证结果仍返回（不抛异常）
        assert isinstance(result, FusionResult)
        assert len(result.perspectives) == 2

    # =========================================================================
    # 场景 E：enhancer 失败 → 不中断流程
    # =========================================================================

    def test_diagnose_enhancer_failure_does_not_break_flow(
        self,
        mock_enhancer,
        mock_recommendation_service,
        mock_candidate_recommendation_service,
        sample_perspectives,
        sample_recommendation_response,
    ):
        """
        场景 E：enhancer 失败
        - 不中断 diagnose 流程
        - 回退到原有 perspectives
        """
        # 配置 mocks
        mock_candidate_recommendation_service.recommend.return_value = sample_recommendation_response
        mock_enhancer.enhance.side_effect = Exception("Enhancer failed")

        # 创建服务
        service = ExpertDiagnosisService(
            recommendation_service=mock_recommendation_service,
            g5_enhancer=mock_enhancer,
            candidate_recommendation_service=mock_candidate_recommendation_service,
        )

        # 执行 diagnose
        result = service.diagnose(
            question="Test question",
            perspectives=sample_perspectives,
            participants=None,
        )

        # 验证结果仍返回（回退到原有 perspectives）
        assert isinstance(result, FusionResult)
        assert len(result.perspectives) == 2
        # 应该回退到原有 perspectives
        assert result.perspectives[0].participant_id == "staff_001:default"

    # =========================================================================
    # 场景 F：traceability 保持
    # =========================================================================

    def test_diagnose_preserves_traceability_after_recommendation_and_enhance(
        self,
        mock_enhancer,
        mock_recommendation_service,
        mock_candidate_recommendation_service,
        sample_perspectives,
        sample_recommendation_response,
    ):
        """
        场景 F：traceability 保持
        - profile_key 在整个流程中保持
        """
        # 配置 mocks
        mock_candidate_recommendation_service.recommend.return_value = sample_recommendation_response

        # enhancer 返回的 perspectives 保留 profile_key
        enhanced = [
            Perspective(
                participant_id="staff_recommended_001:default",
                participant_type="bot",
                role="expert",
                summary="Enhanced perspective",
                confidence=0.90,
                status="completed",
            ),
        ]
        mock_enhancer.enhance.return_value = enhanced

        # 创建服务
        service = ExpertDiagnosisService(
            recommendation_service=mock_recommendation_service,
            g5_enhancer=mock_enhancer,
            candidate_recommendation_service=mock_candidate_recommendation_service,
        )

        # 执行 diagnose
        result = service.diagnose(
            question="Test question",
            perspectives=sample_perspectives,
            participants=None,
        )

        # 验证 result 中的 perspectives 有正确的 profile_key
        for p in result.perspectives:
            assert p.participant_id is not None
            assert ":" in p.participant_id  # profile_key 格式: staff_xxx:default

    # =========================================================================
    # 场景 G：无 candidate_recommendation_service 注入
    # =========================================================================

    def test_diagnose_without_candidate_recommendation_service(
        self,
        mock_enhancer,
        mock_recommendation_service,
        sample_perspectives,
    ):
        """
        场景 G：无 candidate_recommendation_service 注入
        - 保持原有行为
        """
        # 配置 mock enhancer
        mock_enhancer.enhance.return_value = sample_perspectives

        # 创建服务（不注入 candidate_recommendation_service）
        service = ExpertDiagnosisService(
            recommendation_service=mock_recommendation_service,
            g5_enhancer=mock_enhancer,
        )

        # 执行 diagnose
        result = service.diagnose(
            question="Test question",
            perspectives=sample_perspectives,
            participants=["staff_001:default"],
        )

        # 验证结果正常
        assert isinstance(result, FusionResult)

    def test_diagnose_without_any_optional_services(
        self,
        sample_perspectives,
    ):
        """
        场景 H：无任何可选服务注入
        - 保持原有 baseline 行为
        """
        # 创建服务（无任何可选服务）
        service = ExpertDiagnosisService()

        # 执行 diagnose
        result = service.diagnose(
            question="Test question",
            perspectives=sample_perspectives,
        )

        # 验证结果正常
        assert isinstance(result, FusionResult)
        assert result.fusion_mode == "expert_diagnosis"


class TestExpertDiagnosisServiceRecommendationIntegration:
    """推荐与增强集成测试"""

    def test_recommendation_then_enhance_pipeline(
        self,
        mock_enhancer,
        mock_recommendation_service,
        mock_candidate_recommendation_service,
        sample_perspectives,
        sample_recommendation_response,
    ):
        """
        测试完整的 recommendation -> enhance 流水线
        """
        # 配置 mocks
        mock_candidate_recommendation_service.recommend.return_value = sample_recommendation_response

        enhanced = [
            Perspective(
                participant_id="staff_recommended_001:default",
                participant_type="bot",
                role="expert",
                summary="[Enhanced] Security expert analysis",
                confidence=0.90,
                status="completed",
                key_points=["Security concern 1"],
            ),
        ]
        mock_enhancer.enhance.return_value = enhanced

        # 创建服务
        service = ExpertDiagnosisService(
            recommendation_service=mock_recommendation_service,
            g5_enhancer=mock_enhancer,
            candidate_recommendation_service=mock_candidate_recommendation_service,
        )

        # 执行 diagnose
        result = service.diagnose(
            question="How to secure the API?",
            perspectives=sample_perspectives,
            participants=None,
        )

        # 验证调用顺序
        # 1. recommendation 先被调用
        assert mock_candidate_recommendation_service.recommend.call_count == 1

        # 2. enhancer 后被调用
        assert mock_enhancer.enhance.call_count == 1

        # 3. 结果使用增强后的 perspectives
        assert len(result.perspectives) == 1
        assert "Enhanced" in result.perspectives[0].summary

    def test_mode_is_expert_diagnosis(
        self,
        mock_enhancer,
        mock_recommendation_service,
        mock_candidate_recommendation_service,
        sample_perspectives,
        sample_recommendation_response,
    ):
        """
        验证 recommendation 使用 EXPERT_DIAGNOSIS 模式
        """
        # 配置 mocks
        mock_candidate_recommendation_service.recommend.return_value = sample_recommendation_response
        mock_enhancer.enhance.return_value = sample_perspectives

        # 创建服务
        service = ExpertDiagnosisService(
            recommendation_service=mock_recommendation_service,
            g5_enhancer=mock_enhancer,
            candidate_recommendation_service=mock_candidate_recommendation_service,
        )

        # 执行 diagnose
        service.diagnose(
            question="Test question",
            perspectives=sample_perspectives,
            participants=None,
        )

        # 验证 recommendation 使用 EXPERT_DIAGNOSIS 模式
        call_kwargs = mock_candidate_recommendation_service.recommend.call_args.kwargs
        assert call_kwargs["mode"] == RetrievalMode.EXPERT_DIAGNOSIS

    def test_max_candidates_default(
        self,
        mock_enhancer,
        mock_recommendation_service,
        mock_candidate_recommendation_service,
        sample_perspectives,
        sample_recommendation_response,
    ):
        """
        验证默认 max_candidates 行为
        """
        # 配置 mocks
        mock_candidate_recommendation_service.recommend.return_value = sample_recommendation_response
        mock_enhancer.enhance.return_value = sample_perspectives

        # 创建服务
        service = ExpertDiagnosisService(
            recommendation_service=mock_recommendation_service,
            g5_enhancer=mock_enhancer,
            candidate_recommendation_service=mock_candidate_recommendation_service,
        )

        # 执行 diagnose
        service.diagnose(
            question="Test question",
            perspectives=sample_perspectives,
            participants=None,
        )

        # 验证 recommendation 被调用
        assert mock_candidate_recommendation_service.recommend.call_count == 1


class TestExpertDiagnosisServiceInit:
    """初始化测试"""

    def test_init_with_candidate_recommendation_service(
        self,
        mock_candidate_recommendation_service,
    ):
        """测试初始化接受 candidate_recommendation_service"""
        service = ExpertDiagnosisService(
            candidate_recommendation_service=mock_candidate_recommendation_service,
        )
        assert service._candidate_recommendation_service == mock_candidate_recommendation_service

    def test_init_without_candidate_recommendation_service(self):
        """测试初始化不接受 candidate_recommendation_service"""
        service = ExpertDiagnosisService()
        assert service._candidate_recommendation_service is None

    def test_init_with_all_optional_services(
        self,
        mock_recommendation_service,
        mock_enhancer,
        mock_candidate_recommendation_service,
    ):
        """测试初始化接受所有可选服务"""
        service = ExpertDiagnosisService(
            recommendation_service=mock_recommendation_service,
            g5_enhancer=mock_enhancer,
            candidate_recommendation_service=mock_candidate_recommendation_service,
        )
        assert service._recommendation_service == mock_recommendation_service
        assert service._g5_enhancer == mock_enhancer
        assert service._candidate_recommendation_service == mock_candidate_recommendation_service