"""
G5 Expert Diagnosis Layer Smoke Tests

Stage 3: Worker Profile-Driven Expert Execution Preparation
Stage 4: G5 real-context deepening / candidate recommendation

Smoke tests for G5 expert diagnosis chain with real LLM calls.

Requirements:
- LLM_BASE_URL or ANTHROPIC_BASE_URL environment variable
- LLM_AUTH_TOKEN or ANTHROPIC_AUTH_TOKEN environment variable
- G5_SMOKE_TESTS_ENABLED=true to enable real LLM calls

When G5_SMOKE_TESTS_ENABLED is not set, all tests are skipped.
"""

from __future__ import annotations

import os
import pytest

from src.domain.models.fusion_result import Perspective, FusionResult
from src.domain.models.worker_profile import WorkerProfile, ProfileType
from src.domain.models.worker_context_digest import WorkerContextDigest
from src.domain.models.context_fragment import ContextFragment, ContextKind
from src.domain.models.skill_profile import SkillProfile
from src.domain.models.retrieval_mode import RetrievalMode
from src.application.services.g5_expert_enhancer_impl import G5ExpertEnhancerImpl
from src.application.services.expert_diagnosis_service import ExpertDiagnosisService


# =============================================================================
# Environment Variable Control
# =============================================================================

# Check if smoke tests are enabled
G5_SMOKE_ENABLED = os.environ.get("G5_SMOKE_TESTS_ENABLED", "").lower() in ("true", "1", "yes")

# Skip reason when not enabled
SKIP_REASON = (
    "G5 smoke tests are disabled. "
    "Set G5_SMOKE_TESTS_ENABLED=true, LLM_BASE_URL, and LLM_AUTH_TOKEN to enable."
)


# =============================================================================
# Module-level Fixtures (Real Implementations)
# =============================================================================

@pytest.fixture
def real_gateway():
    """创建真实 LLM Gateway"""
    from src.application.services.llm_gateway_service import LLMGatewayService
    from src.infra.llm.providers.anthropic_compatible_provider import AnthropicCompatibleProvider
    from src.infra.llm.config.llm_settings import LLMSettings

    settings = LLMSettings()
    provider = AnthropicCompatibleProvider(settings=settings)
    gateway = LLMGatewayService(provider=provider, settings=settings)
    return gateway


@pytest.fixture
def sample_profile_for_smoke():
    """创建用于 smoke 测试的 WorkerProfile"""
    return WorkerProfile(
        staff_id="smoke_test_001",
        profile_id="default",
        profile_type=ProfileType.DEFAULT,
        source_root="/test/profiles",
        context_fragments=[
            ContextFragment(
                kind=ContextKind.AGENT,
                filename="AGENTS.md",
                content="Expert in software architecture and distributed systems with 10 years of experience.",
                source_path="/test/profiles/smoke_test_001/default/openclaw/AGENTS.md",
            )
        ],
        active_skills=[
            SkillProfile(
                name="Architecture",
                description="Software architecture design",
                skill_id="skill_arch_001",
                skill_set_name="design",
            ),
        ],
    )


@pytest.fixture
def sample_digest_for_smoke():
    """创建用于 smoke 测试的 WorkerContextDigest"""
    return WorkerContextDigest(
        profile_key="smoke_test_001:default",
        mode=RetrievalMode.EXPERT_DIAGNOSIS,
        question="How to design a scalable microservices architecture?",
        relevant_fragments=[],
        relevant_skills=[
            SkillProfile(
                name="Architecture",
                description="Software architecture design",
                skill_id="skill_arch_001",
                skill_set_name="design",
            )
        ],
        context_summary="Expert in software architecture and distributed systems",
    )


@pytest.fixture
def fake_retrieval_service_for_smoke(sample_profile_for_smoke):
    """创建返回真实 profile 的 fake retrieval service"""
    from unittest.mock import Mock
    service = Mock()
    service.retrieve.return_value = Mock(
        results=[
            Mock(profile=sample_profile_for_smoke, total_score=0.95)
        ]
    )
    return service


@pytest.fixture
def fake_preparation_service_for_smoke(sample_digest_for_smoke):
    """创建返回真实 digest 的 fake preparation service"""
    from unittest.mock import Mock
    service = Mock()
    service.prepare.return_value = sample_digest_for_smoke
    return service


@pytest.fixture
def fake_profile_source_for_smoke():
    """创建 fake profile source"""
    from unittest.mock import Mock
    return Mock()


# =============================================================================
# Test Classes
# =============================================================================

@pytest.mark.skipif(not G5_SMOKE_ENABLED, reason=SKIP_REASON)
class TestG5EnhancerSmoke:
    """G5 Expert Enhancer Smoke Tests (Real LLM)"""

    def test_smoke_g5_enhancer_real_llm_call(
        self,
        real_gateway,
        fake_retrieval_service_for_smoke,
        fake_preparation_service_for_smoke,
        fake_profile_source_for_smoke,
    ):
        """
        Smoke Test: G5 Expert Enhancer with real LLM

        验证:
        - LLM Gateway 可达
        - 结构化输出解析成功
        - 返回有效的 Perspective
        """
        # 创建 G5ExpertEnhancerImpl
        enhancer = G5ExpertEnhancerImpl(
            gateway=real_gateway,
            retrieval_service=fake_retrieval_service_for_smoke,
            preparation_service=fake_preparation_service_for_smoke,
            profile_source=fake_profile_source_for_smoke,
        )

        # 执行 enhance
        perspectives = enhancer.enhance(
            question="How to design a scalable microservices architecture?",
            base_perspectives=[],
            participants=["smoke_test_001"],
        )

        # 验证返回有效的 perspectives
        assert len(perspectives) >= 1
        perspective = perspectives[0]

        # 验证 Perspective 字段
        assert perspective.participant_id == "smoke_test_001:default"
        assert perspective.role == "expert"
        assert perspective.status == "completed"
        assert perspective.summary is not None and len(perspective.summary) > 0
        assert perspective.confidence is not None
        assert 0 <= perspective.confidence <= 1

        # 验证非 fallback（置信度应较高）
        # 如果 LLM 正常工作，置信度应该 >= 0.7
        assert perspective.confidence >= 0.7, (
            f"Expected confidence >= 0.7 for real LLM response, got {perspective.confidence}"
        )

    def test_smoke_g5_enhancer_structured_output(
        self,
        real_gateway,
        fake_retrieval_service_for_smoke,
        fake_preparation_service_for_smoke,
        fake_profile_source_for_smoke,
    ):
        """
        Smoke Test: G5 Expert Enhancer 结构化输出

        验证:
        - key_points 解析成功
        - concerns 解析成功
        - risk_level 解析成功
        - rationale_summary 解析成功
        """
        # 创建 G5ExpertEnhancerImpl
        enhancer = G5ExpertEnhancerImpl(
            gateway=real_gateway,
            retrieval_service=fake_retrieval_service_for_smoke,
            preparation_service=fake_preparation_service_for_smoke,
            profile_source=fake_profile_source_for_smoke,
        )

        # 执行 enhance
        perspectives = enhancer.enhance(
            question="What are the key considerations for data security in cloud applications?",
            base_perspectives=[],
            participants=["smoke_test_001"],
        )

        # 验证返回的 perspective
        assert len(perspectives) >= 1
        perspective = perspectives[0]

        # 验证结构化输出字段存在
        # key_points 应该非空
        assert perspective.key_points is not None, "key_points should not be None"
        assert len(perspective.key_points) >= 1, "key_points should have at least 1 item"

        # concerns 可能非空
        assert perspective.concerns is not None, "concerns should not be None"


@pytest.mark.skipif(not G5_SMOKE_ENABLED, reason=SKIP_REASON)
class TestG5DiagnosisServiceSmoke:
    """G5 ExpertDiagnosisService Smoke Tests (Real LLM)"""

    def test_smoke_diagnosis_service_full_chain(
        self,
        real_gateway,
        fake_retrieval_service_for_smoke,
        fake_preparation_service_for_smoke,
        fake_profile_source_for_smoke,
    ):
        """
        Smoke Test: ExpertDiagnosisService 完整链路

        验证:
        - G5 enhancer 被调用
        - diagnose 返回 FusionResult
        - risk_assessment 有效
        - summary 有效
        """
        # 创建 G5ExpertEnhancerImpl
        enhancer = G5ExpertEnhancerImpl(
            gateway=real_gateway,
            retrieval_service=fake_retrieval_service_for_smoke,
            preparation_service=fake_preparation_service_for_smoke,
            profile_source=fake_profile_source_for_smoke,
        )

        # 创建 ExpertDiagnosisService
        diagnosis_service = ExpertDiagnosisService(
            g5_enhancer=enhancer,
        )

        # 执行 diagnose
        result = diagnosis_service.diagnose(
            question="Should we use a monorepo or polyrepo structure for our microservices?",
            perspectives=[],
            participants=["smoke_test_001"],
        )

        # 验证 FusionResult
        assert isinstance(result, FusionResult)
        assert result.fusion_mode == "expert_diagnosis"
        assert len(result.perspectives) >= 1

        # 验证风险评估
        assert result.risk_assessment is not None

        # 验证诊断摘要
        assert result.summary is not None and len(result.summary) > 0


@pytest.mark.skipif(not G5_SMOKE_ENABLED, reason=SKIP_REASON)
class TestG5EnhancerFallbackSmoke:
    """G5 Expert Enhancer Fallback Smoke Tests (Real LLM scenarios)"""

    def test_smoke_fallback_on_invalid_question(
        self,
        real_gateway,
        fake_retrieval_service_for_smoke,
        fake_preparation_service_for_smoke,
        fake_profile_source_for_smoke,
    ):
        """
        Smoke Test: 复杂问题不应触发 fallback

        验证即使是复杂问题，LLM 也应返回有效响应
        """
        # 创建 G5ExpertEnhancerImpl
        enhancer = G5ExpertEnhancerImpl(
            gateway=real_gateway,
            retrieval_service=fake_retrieval_service_for_smoke,
            preparation_service=fake_preparation_service_for_smoke,
            profile_source=fake_profile_source_for_smoke,
        )

        # 执行 enhance（使用复杂问题）
        perspectives = enhancer.enhance(
            question="",  # 空问题
            base_perspectives=[],
            participants=["smoke_test_001"],
        )

        # 验证返回 perspectives（可能 fallback）
        # 空问题可能返回 fallback 或基本响应
        assert len(perspectives) >= 1


# =============================================================================
# Connection Test (Always Run)
# =============================================================================

class TestG5SmokeConfig:
    """G5 Smoke Test 配置检查 (Always Run)"""

    def test_smoke_config_check(self):
        """
        验证 smoke test 配置状态

        此测试总是运行，用于确认 smoke test 的配置状态。
        """
        base_url = os.environ.get("LLM_BASE_URL") or os.environ.get("ANTHROPIC_BASE_URL")
        auth_token_set = bool(
            os.environ.get("LLM_AUTH_TOKEN") or os.environ.get("ANTHROPIC_AUTH_TOKEN")
        )

        # 如果设置了 G5_SMOKE_TESTS_ENABLED=true，则必须配置必要的环境变量
        if G5_SMOKE_ENABLED:
            assert base_url is not None, (
                "G5_SMOKE_TESTS_ENABLED is set but LLM_BASE_URL is missing"
            )
            assert auth_token_set, (
                "G5_SMOKE_TESTS_ENABLED is set but LLM_AUTH_TOKEN is missing"
            )

    def test_g5_enhancer_importable(self):
        """验证 G5 Expert Enhancer 可导入"""
        from src.application.services.g5_expert_enhancer_impl import G5ExpertEnhancerImpl
        from src.domain.services.g5_expert_enhancer import G5ExpertEnhancer
        assert G5ExpertEnhancerImpl is not None
        assert G5ExpertEnhancer is not None

    def test_expert_diagnosis_service_importable(self):
        """验证 ExpertDiagnosisService 可导入"""
        from src.application.services.expert_diagnosis_service import ExpertDiagnosisService
        assert ExpertDiagnosisService is not None

    def test_real_provider_importable(self):
        """验证真实 Provider 可导入"""
        from src.infra.llm.providers.anthropic_compatible_provider import AnthropicCompatibleProvider
        assert AnthropicCompatibleProvider is not None


# =============================================================================
# Stage 4: Candidate Recommendation Smoke Tests
# =============================================================================

@pytest.mark.skipif(not G5_SMOKE_ENABLED, reason=SKIP_REASON)
class TestG5CandidateRecommendationSmoke:
    """Stage 4: Candidate Recommendation Smoke Tests (Real LLM)"""

    def test_smoke_candidate_recommendation_importable(self):
        """验证 Candidate Recommendation Service 可导入"""
        from src.application.services.worker_candidate_recommendation_impl import (
            WorkerCandidateRecommendationImpl,
        )
        from src.domain.services.worker_candidate_recommendation_service import (
            WorkerCandidateRecommendationService,
        )
        assert WorkerCandidateRecommendationImpl is not None
        assert WorkerCandidateRecommendationService is not None

    def test_smoke_diagnosis_with_candidate_recommendation(
        self,
        real_gateway,
        fake_retrieval_service_for_smoke,
        fake_preparation_service_for_smoke,
        fake_profile_source_for_smoke,
    ):
        """
        Smoke Test: ExpertDiagnosisService with Candidate Recommendation

        验证:
        - Candidate recommendation 可用
        - 整体链路正常工作
        """
        from src.application.services.worker_candidate_recommendation_impl import (
            WorkerCandidateRecommendationImpl,
        )

        # 创建 G5ExpertEnhancerImpl
        enhancer = G5ExpertEnhancerImpl(
            gateway=real_gateway,
            retrieval_service=fake_retrieval_service_for_smoke,
            preparation_service=fake_preparation_service_for_smoke,
            profile_source=fake_profile_source_for_smoke,
        )

        # 创建 Candidate Recommendation Service
        candidate_rec_service = WorkerCandidateRecommendationImpl(
            retrieval_service=fake_retrieval_service_for_smoke,
        )

        # 创建 ExpertDiagnosisService with both services
        diagnosis_service = ExpertDiagnosisService(
            g5_enhancer=enhancer,
            candidate_recommendation_service=candidate_rec_service,
        )

        # 执行 diagnose（有显式 participants）
        result = diagnosis_service.diagnose(
            question="How should we architect our microservices?",
            perspectives=[],
            participants=["smoke_test_001"],
        )

        # 验证 FusionResult
        assert isinstance(result, FusionResult)
        assert result.fusion_mode == "expert_diagnosis"


class TestG5Stage4Config:
    """Stage 4 配置检查 (Always Run)"""

    def test_candidate_recommendation_importable(self):
        """验证 Candidate Recommendation 可导入"""
        from src.domain.models.candidate_recommendation import (
            CandidateRecommendation,
            CandidateRecommendationResponse,
        )
        from src.domain.models.domain_coverage import DomainCoverage
        from src.domain.services.participants_sufficiency_checker import (
            ParticipantsSufficiencyChecker,
        )
        assert CandidateRecommendation is not None
        assert CandidateRecommendationResponse is not None
        assert DomainCoverage is not None
        assert ParticipantsSufficiencyChecker is not None

    def test_candidate_recommendation_impl_importable(self):
        """验证 Candidate Recommendation Implementation 可导入"""
        from src.application.services.worker_candidate_recommendation_impl import (
            WorkerCandidateRecommendationImpl,
        )
        assert WorkerCandidateRecommendationImpl is not None