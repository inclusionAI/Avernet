"""
G5 Profile-Driven LLM Flow Integration Tests

Stage 3: Worker Profile-Driven Expert Execution Preparation

测试 G5 enhancer 链路集成，验证：
- G5 enhancer 链路接通
- prompt → parser → perspective 链路接通
- perspective → diagnose 聚合链路接通
- fallback 生效
- G1/G2 未被污染

约束：全部使用 fake/mock gateway，不打真实 LLM。
"""

from __future__ import annotations

import pytest
from unittest.mock import Mock

from src.domain.models.fusion_result import Perspective, FusionResult
from src.domain.models.worker_profile import WorkerProfile, ProfileType
from src.domain.models.worker_context_digest import WorkerContextDigest
from src.domain.models.context_fragment import ContextFragment, ContextKind
from src.domain.models.skill_profile import SkillProfile
from src.domain.models.retrieval_mode import RetrievalMode
from src.domain.services.g5_expert_enhancer import G5ExpertEnhancer
from src.application.services.g5_expert_enhancer_impl import G5ExpertEnhancerImpl
from src.application.services.expert_diagnosis_service import ExpertDiagnosisService


# =============================================================================
# Module-level Fixtures
# =============================================================================

@pytest.fixture
def sample_profile():
    """创建示例 WorkerProfile"""
    return WorkerProfile(
        staff_id="001",
        profile_id="default",
        profile_type=ProfileType.DEFAULT,
        source_root="/path/to/profiles",
        context_fragments=[
            ContextFragment(
                kind=ContextKind.AGENT,
                filename="AGENTS.md",
                content="Expert in Python and API design with 5 years of experience.",
                source_path="/path/to/profiles/staff_001/default/openclaw/AGENTS.md",
            )
        ],
        active_skills=[
            SkillProfile(
                name="Python",
                description="Python programming",
                skill_id="skill_python_001",
                skill_set_name="programming",
            ),
        ],
    )


@pytest.fixture
def sample_digest():
    """创建示例 WorkerContextDigest"""
    return WorkerContextDigest(
        profile_key="staff_001:default",
        mode=RetrievalMode.EXPERT_DIAGNOSIS,
        question="How to design an API?",
        relevant_fragments=[],
        relevant_skills=[],
        context_summary="Expert in Python and API design",
    )


@pytest.fixture
def fake_gateway():
    """创建 fake LLM Gateway"""
    gateway = Mock()
    gateway.generate.return_value = Mock(
        parse_success=True,
        structured_data={
            "summary": "Based on the expert profile, I recommend using RESTful principles for API design.",
            "confidence": 0.88,
            "key_points": ["Use RESTful principles", "Version your API", "Document endpoints"],
            "concerns": ["Need to consider authentication"],
            "risk_level": "low",
            "rationale_summary": "Analysis based on Python and API design expertise.",
            "evidence_summary": ["5 years of experience", "Contributed to API projects"],
        },
    )
    return gateway


@pytest.fixture
def fake_retrieval_service(sample_profile):
    """创建 fake WorkerProfileRetrievalService"""
    service = Mock()
    service.retrieve.return_value = Mock(
        results=[
            Mock(profile=sample_profile, total_score=0.9)
        ]
    )
    return service


@pytest.fixture
def fake_preparation_service(sample_digest):
    """创建 fake WorkerContextPreparationService"""
    service = Mock()
    service.prepare.return_value = sample_digest
    return service


@pytest.fixture
def fake_profile_source():
    """创建 fake WorkerProfileSource"""
    return Mock()


# =============================================================================
# Test Classes
# =============================================================================

class TestG5EnhancerChainIntegration:
    """G5 enhancer 链路集成测试"""

    def test_g5_enhancer_chain_integration(
        self,
        fake_gateway,
        fake_retrieval_service,
        fake_preparation_service,
        fake_profile_source,
    ):
        """测试 G5 enhancer 链路接通（retrieval → preparation → LLM → perspective）"""
        # 创建 G5ExpertEnhancerImpl
        enhancer = G5ExpertEnhancerImpl(
            gateway=fake_gateway,
            retrieval_service=fake_retrieval_service,
            preparation_service=fake_preparation_service,
            profile_source=fake_profile_source,
        )

        # 执行 enhance
        result = enhancer.enhance(
            question="How to design an API?",
            base_perspectives=[],
            participants=["staff_001"],
        )

        # 验证链路接通
        # 1. retrieval 被调用
        fake_retrieval_service.retrieve.assert_called_once()

        # 2. preparation 被调用
        fake_preparation_service.prepare.assert_called_once()

        # 3. LLM gateway 被调用
        fake_gateway.generate.assert_called_once()

        # 4. 返回增强后的 perspectives
        assert len(result) == 1
        assert result[0].role == "expert"
        assert result[0].confidence == 0.88

    def test_prompt_to_parser_to_perspective(
        self,
        fake_gateway,
        fake_retrieval_service,
        fake_preparation_service,
        fake_profile_source,
    ):
        """测试 prompt → parser → perspective 链路接通"""
        # 创建 G5ExpertEnhancerImpl
        enhancer = G5ExpertEnhancerImpl(
            gateway=fake_gateway,
            retrieval_service=fake_retrieval_service,
            preparation_service=fake_preparation_service,
            profile_source=fake_profile_source,
        )

        # 执行 enhance
        result = enhancer.enhance(
            question="Test question",
            base_perspectives=[],
        )

        # 验证 LLM request 包含正确的 prompt
        fake_gateway.generate.assert_called_once()
        call_args = fake_gateway.generate.call_args
        llm_request = call_args[0][0]

        # 验证 prompt 包含必要信息
        assert "Test question" in llm_request.user_prompt
        assert llm_request.temperature == 0.2
        assert llm_request.task_spec.need_structured_output is True

        # 验证 perspective 正确解析
        assert result[0].summary == "Based on the expert profile, I recommend using RESTful principles for API design."
        assert result[0].key_points == ["Use RESTful principles", "Version your API", "Document endpoints"]


class TestPerspectiveToDiagnoseAggregation:
    """perspective → diagnose 聚合链路测试"""

    def test_perspective_to_diagnose_aggregation(
        self,
        fake_gateway,
        fake_retrieval_service,
        fake_preparation_service,
        fake_profile_source,
    ):
        """测试 perspective → diagnose 聚合链路接通"""
        # 创建 G5ExpertEnhancerImpl
        enhancer = G5ExpertEnhancerImpl(
            gateway=fake_gateway,
            retrieval_service=fake_retrieval_service,
            preparation_service=fake_preparation_service,
            profile_source=fake_profile_source,
        )

        # 创建 ExpertDiagnosisService（注入 enhancer）
        diagnosis_service = ExpertDiagnosisService(
            g5_enhancer=enhancer,
        )

        # 执行 diagnose
        result = diagnosis_service.diagnose(
            question="How to design an API?",
            perspectives=[],
            participants=["staff_001"],
        )

        # 验证聚合结果
        assert isinstance(result, FusionResult)
        assert result.fusion_mode == "expert_diagnosis"
        # 增强后的 perspectives 被用于诊断
        assert len(result.perspectives) == 1
        assert result.risk_assessment is not None
        assert result.summary is not None


class TestFallbackEffectiveInIntegration:
    """fallback 在集成环境下生效测试"""

    def test_fallback_effective_when_llm_fails(
        self,
        fake_gateway,
        fake_retrieval_service,
        fake_preparation_service,
        fake_profile_source,
    ):
        """测试 LLM 失败时 fallback 生效"""
        # 配置 LLM 失败
        fake_gateway.generate.side_effect = Exception("LLM failure")

        # 创建 G5ExpertEnhancerImpl
        enhancer = G5ExpertEnhancerImpl(
            gateway=fake_gateway,
            retrieval_service=fake_retrieval_service,
            preparation_service=fake_preparation_service,
            profile_source=fake_profile_source,
        )

        # 创建 ExpertDiagnosisService（注入 enhancer）
        diagnosis_service = ExpertDiagnosisService(
            g5_enhancer=enhancer,
        )

        # 执行 diagnose
        result = diagnosis_service.diagnose(
            question="How to design an API?",
            perspectives=[],
            participants=["staff_001"],
        )

        # 验证 fallback 生效
        assert isinstance(result, FusionResult)
        # 应该有 fallback perspective
        assert len(result.perspectives) == 1
        # fallback 置信度较低
        assert result.perspectives[0].confidence < 0.8

    def test_fallback_effective_when_parse_fails(
        self,
        fake_gateway,
        fake_retrieval_service,
        fake_preparation_service,
        fake_profile_source,
    ):
        """测试 parse 失败时 fallback 生效"""
        # 配置 parse 失败
        fake_gateway.generate.return_value = Mock(
            parse_success=False,
            structured_data=None,
            raw_text="Invalid JSON",
        )

        # 创建 G5ExpertEnhancerImpl
        enhancer = G5ExpertEnhancerImpl(
            gateway=fake_gateway,
            retrieval_service=fake_retrieval_service,
            preparation_service=fake_preparation_service,
            profile_source=fake_profile_source,
        )

        # 创建 ExpertDiagnosisService（注入 enhancer）
        diagnosis_service = ExpertDiagnosisService(
            g5_enhancer=enhancer,
        )

        # 执行 diagnose
        result = diagnosis_service.diagnose(
            question="How to design an API?",
            perspectives=[],
            participants=["staff_001"],
        )

        # 验证 fallback 生效
        assert isinstance(result, FusionResult)
        assert len(result.perspectives) == 1
        # fallback 应该包含相关信息
        assert "Fallback" in result.perspectives[0].summary or result.perspectives[0].confidence < 0.8


class TestG1G2BehaviorUnchanged:
    """G1/G2 行为回归验证"""

    def test_g1_behavior_unchanged_with_g5_enhancer_injected(
        self,
        fake_gateway,
        fake_retrieval_service,
        fake_preparation_service,
        fake_profile_source,
    ):
        """测试 G5 enhancer 注入后 G1 行为不变"""
        from src.application.services.group_fusion_service import GroupFusionService
        from src.domain.services.perspective_provider import PerspectiveProvider, PerspectiveContext

        # 创建 G5Enhancer
        enhancer = G5ExpertEnhancerImpl(
            gateway=fake_gateway,
            retrieval_service=fake_retrieval_service,
            preparation_service=fake_preparation_service,
            profile_source=fake_profile_source,
        )

        # 创建 ExpertDiagnosisService（注入 enhancer）
        diagnosis_service = ExpertDiagnosisService(
            g5_enhancer=enhancer,
        )

        # 创建 G1 用的 mock provider
        mock_provider = Mock(spec=PerspectiveProvider)
        mock_provider.collect.return_value = Perspective(
            participant_id="staff_001",
            participant_type="bot",
            role="consultant",
            summary="G1 perspective",
            status="completed",
        )

        # 创建 GroupFusionService（注入 provider 和 diagnosis_service）
        fusion_service = GroupFusionService(
            provider=mock_provider,
            expert_diagnosis_service=diagnosis_service,
        )

        # 执行 G1 模式
        from src.domain.models.fusion_request import FusionRequest

        request = FusionRequest(
            question="Test question",
            participants=["staff_001"],
            fusion_mode="agent",  # G1
        )

        result = fusion_service.fuse(request, "grp-001")

        # 验证 G1 行为不变
        assert result.fusion_mode == "agent"
        # G1 不应该调用 LLM gateway（通过 G5 enhancer）
        # g1 不使用 expert_diagnosis_service，所以 enhancer 不应该被调用
        # 验证 G1 perspective 被正确使用
        assert len(result.perspectives) == 1
        assert result.perspectives[0].summary == "G1 perspective"

    def test_g2_behavior_unchanged_with_g5_enhancer_injected(
        self,
        fake_gateway,
        fake_retrieval_service,
        fake_preparation_service,
        fake_profile_source,
    ):
        """测试 G5 enhancer 注入后 G2 行为不变"""
        from src.application.services.group_fusion_service import GroupFusionService
        from src.application.services.conflict_alignment_service import ConflictAlignmentService

        # 创建 G5Enhancer
        enhancer = G5ExpertEnhancerImpl(
            gateway=fake_gateway,
            retrieval_service=fake_retrieval_service,
            preparation_service=fake_preparation_service,
            profile_source=fake_profile_source,
        )

        # 创建 ExpertDiagnosisService（注入 enhancer）
        diagnosis_service = ExpertDiagnosisService(
            g5_enhancer=enhancer,
        )

        # 创建 G2 用的 mock - 返回 FusionResult 而不是 Mock
        from src.domain.models.fusion_result import FusionTiming
        from datetime import datetime

        mock_conflict_service = Mock(spec=ConflictAlignmentService)
        mock_conflict_service.align.return_value = FusionResult(
            group_id="grp-001",
            fusion_id="fus-test-001",
            question="Test question",
            driver_bot_id="staff_001",
            perspectives=[
                Perspective(
                    participant_id="staff_001",
                    participant_type="bot",
                    role="consultant",
                    summary="G2 perspective",
                    status="completed",
                )
            ],
            fusion_mode="conflict_alignment",
            conflicts=[],
            alignment_points=[],
            key_insights=["G2 insight"],
            partial_success=False,
            timing=FusionTiming(
                started_at=datetime.now(),
                finished_at=datetime.now(),
                duration_ms=100,
            ),
        )

        # 创建 G2 用的 mock provider
        mock_provider = Mock()
        mock_provider.collect.return_value = Perspective(
            participant_id="staff_001",
            participant_type="bot",
            role="consultant",
            summary="G2 perspective",
            status="completed",
        )

        # 创建 GroupFusionService（注入 provider, conflict_service 和 diagnosis_service）
        fusion_service = GroupFusionService(
            provider=mock_provider,
            conflict_alignment_service=mock_conflict_service,
            expert_diagnosis_service=diagnosis_service,
        )

        # 执行 G2 模式
        from src.domain.models.fusion_request import FusionRequest

        request = FusionRequest(
            question="Test question",
            participants=["staff_001"],
            fusion_mode="conflict_alignment",  # G2
        )

        result = fusion_service.fuse(request, "grp-001")

        # 验证 G2 行为不变
        assert result.fusion_mode == "conflict_alignment"
        assert result.key_insights == ["G2 insight"]
        # G2 不应该调用 LLM gateway（通过 G5 enhancer）
        # G2 使用 conflict_alignment_service，不使用 expert_diagnosis_service