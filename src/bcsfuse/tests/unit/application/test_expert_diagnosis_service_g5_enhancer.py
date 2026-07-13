"""
Tests for ExpertDiagnosisService with G5ExpertEnhancer

Stage 3: Worker Profile-Driven Expert Execution Preparation

测试 ExpertDiagnosisService 与 G5ExpertEnhancer 的集成。
"""

from __future__ import annotations

import pytest
from unittest.mock import Mock

from src.domain.models.fusion_result import Perspective, FusionResult
from src.domain.models.worker_profile import WorkerProfile
from src.domain.models.worker_context_digest import WorkerContextDigest
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


# =============================================================================
# Test Classes
# =============================================================================

class TestExpertDiagnosisServiceWithEnhancer:
    """ExpertDiagnosisService 与 G5ExpertEnhancer 集成测试"""

    def test_diagnose_with_g5_enhancer(
        self,
        mock_enhancer,
        mock_recommendation_service,
        sample_perspectives,
        enhanced_perspectives,
    ):
        """测试注入 enhancer 后 diagnose 正常调用 enhance"""
        # 配置 mock enhancer
        mock_enhancer.enhance.return_value = enhanced_perspectives

        # 创建服务
        service = ExpertDiagnosisService(
            recommendation_service=mock_recommendation_service,
            g5_enhancer=mock_enhancer,
        )

        # 执行 diagnose
        result = service.diagnose(
            question="How to optimize database queries?",
            perspectives=sample_perspectives,
            participants=["staff_001", "staff_002"],
        )

        # 验证 enhancer 被调用
        mock_enhancer.enhance.assert_called_once()
        call_kwargs = mock_enhancer.enhance.call_args.kwargs
        assert call_kwargs["question"] == "How to optimize database queries?"
        assert call_kwargs["participants"] == ["staff_001", "staff_002"]

        # 验证结果使用增强后的 perspectives
        assert len(result.perspectives) == 2
        assert result.perspectives[0].participant_id == "staff_003:default"

    def test_diagnose_without_g5_enhancer(
        self,
        mock_recommendation_service,
        sample_perspectives,
    ):
        """测试未注入 enhancer 时 diagnose 保持原有行为"""
        # 创建服务（不注入 enhancer）
        service = ExpertDiagnosisService(
            recommendation_service=mock_recommendation_service,
        )

        # 执行 diagnose
        result = service.diagnose(
            question="Test question",
            perspectives=sample_perspectives,
        )

        # 验证结果使用原有 perspectives
        assert len(result.perspectives) == 2
        assert result.perspectives[0].participant_id == "staff_001:default"

    def test_enhance_can_recommend_when_participants_missing(
        self,
        mock_enhancer,
        mock_recommendation_service,
        sample_perspectives,
        enhanced_perspectives,
    ):
        """测试 participants=None 时 enhancer 仍可基于 retrieval 推荐专家"""
        # 配置 mock enhancer（基于 question + base_perspectives 推荐）
        mock_enhancer.enhance.return_value = enhanced_perspectives

        # 创建服务
        service = ExpertDiagnosisService(
            recommendation_service=mock_recommendation_service,
            g5_enhancer=mock_enhancer,
        )

        # 执行 diagnose（不传 participants）
        result = service.diagnose(
            question="How to design a scalable API?",
            perspectives=sample_perspectives,
            participants=None,  # 不传 participants
        )

        # 验证 enhancer 仍被调用
        mock_enhancer.enhance.assert_called_once()
        call_kwargs = mock_enhancer.enhance.call_args.kwargs

        # question 和 base_perspectives 被传递
        assert call_kwargs["question"] == "How to design a scalable API?"
        assert len(call_kwargs["base_perspectives"]) == 2

        # participants 可能是 None 或空列表
        assert call_kwargs.get("participants") is None or call_kwargs.get("participants") == []

        # 结果使用增强后的 perspectives
        assert len(result.perspectives) == 2

    def test_enhance_failure_does_not_break_diagnose(
        self,
        mock_enhancer,
        mock_recommendation_service,
        sample_perspectives,
    ):
        """测试 enhance 失败不中断 diagnose 流程"""
        # 配置 mock enhancer 抛出异常
        mock_enhancer.enhance.side_effect = Exception("Enhancer failure")

        # 创建服务
        service = ExpertDiagnosisService(
            recommendation_service=mock_recommendation_service,
            g5_enhancer=mock_enhancer,
        )

        # 执行 diagnose
        result = service.diagnose(
            question="Test question",
            perspectives=sample_perspectives,
            participants=["staff_001"],
        )

        # 验证结果仍返回（使用原有 perspectives）
        assert isinstance(result, FusionResult)
        assert len(result.perspectives) == 2
        # 应该回退到原有 perspectives
        assert result.perspectives[0].participant_id == "staff_001:default"

    def test_enhanced_perspectives_used_in_analysis(
        self,
        mock_enhancer,
        mock_recommendation_service,
        sample_perspectives,
        enhanced_perspectives,
    ):
        """测试增强后的 perspectives 被用于风险评估"""
        # 配置 mock enhancer
        mock_enhancer.enhance.return_value = enhanced_perspectives

        # 创建服务
        service = ExpertDiagnosisService(
            recommendation_service=mock_recommendation_service,
            g5_enhancer=mock_enhancer,
        )

        # 执行 diagnose
        result = service.diagnose(
            question="Test question",
            perspectives=sample_perspectives,
            participants=["staff_001"],
        )

        # 验证风险评估基于增强后的 perspectives
        # enhanced_perspectives 有 2 个专家
        assert result.risk_assessment is not None
        # 验证 summary 中包含增强后的专家数量
        assert "2 位专家" in result.summary or "2" in result.summary

    def test_participants_passed_to_enhance(
        self,
        mock_enhancer,
        mock_recommendation_service,
        sample_perspectives,
        enhanced_perspectives,
    ):
        """测试 participants 参数正确传递"""
        # 配置 mock enhancer
        mock_enhancer.enhance.return_value = enhanced_perspectives

        # 创建服务
        service = ExpertDiagnosisService(
            recommendation_service=mock_recommendation_service,
            g5_enhancer=mock_enhancer,
        )

        participants_list = ["staff_001:default", "staff_002:default", "staff_003:default"]

        # 执行 diagnose
        service.diagnose(
            question="Test question",
            perspectives=sample_perspectives,
            participants=participants_list,
        )

        # 验证 participants 被正确传递
        mock_enhancer.enhance.assert_called_once()
        call_kwargs = mock_enhancer.enhance.call_args.kwargs
        assert call_kwargs["participants"] == participants_list


class TestExpertDiagnosisServiceEnhancerFallback:
    """ExpertDiagnosisService enhancer fallback 测试"""

    def test_enhance_returns_empty_falls_back_to_original(
        self,
        mock_enhancer,
        mock_recommendation_service,
        sample_perspectives,
    ):
        """测试 enhance 返回空列表时回退到原有 perspectives"""
        # 配置 mock enhancer 返回空列表
        mock_enhancer.enhance.return_value = []

        # 创建服务
        service = ExpertDiagnosisService(
            recommendation_service=mock_recommendation_service,
            g5_enhancer=mock_enhancer,
        )

        # 执行 diagnose
        result = service.diagnose(
            question="Test question",
            perspectives=sample_perspectives,
            participants=["staff_001"],
        )

        # 验证回退到原有 perspectives
        assert len(result.perspectives) == 2
        assert result.perspectives[0].participant_id == "staff_001:default"

    def test_enhance_returns_fallback_perspectives(
        self,
        mock_enhancer,
        mock_recommendation_service,
        sample_perspectives,
    ):
        """测试 enhance 返回 fallback perspectives（低置信度）"""
        # 配置 mock enhancer 返回 fallback perspectives
        fallback_perspectives = [
            Perspective(
                participant_id="staff_fallback:default",
                participant_type="bot",
                role="expert",
                summary="[Fallback] Generated without LLM",
                confidence=0.5,  # fallback 低置信度
                status="completed",
                concerns=["LLM generation failed, using fallback"],
            )
        ]
        mock_enhancer.enhance.return_value = fallback_perspectives

        # 创建服务
        service = ExpertDiagnosisService(
            recommendation_service=mock_recommendation_service,
            g5_enhancer=mock_enhancer,
        )

        # 执行 diagnose
        result = service.diagnose(
            question="Test question",
            perspectives=sample_perspectives,
            participants=["staff_001"],
        )

        # 验证使用 fallback perspectives
        assert len(result.perspectives) == 1
        assert result.perspectives[0].confidence == 0.5
        assert "Fallback" in result.perspectives[0].summary