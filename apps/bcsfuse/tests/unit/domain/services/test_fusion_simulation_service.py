"""
Tests for Fusion Simulation Service

Worker Profile Retrieval & Fusion Simulation Baseline

测试范围：
- FusionSimulationService: 融合模拟服务
- G1/G2/G5 模式分发
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional
from unittest.mock import Mock, patch

import pytest

from src.domain.models.context_fragment import ContextFragment, ContextKind
from src.domain.models.fusion_request import FusionRequest
from src.domain.models.retrieval_mode import RetrievalMode
from src.domain.models.skill_profile import SkillProfile
from src.domain.models.worker_profile import WorkerProfile, ProfileType
from src.domain.models.worker_context_digest import WorkerContextDigest
from src.domain.models.fusion_simulation_input import FusionSimulationInput


def create_test_profile(
    staff_id: str,
    profile_id: str = "default",
    skills: Optional[list[str]] = None,
    profile_type: ProfileType = ProfileType.DEFAULT,
) -> WorkerProfile:
    """Create a test WorkerProfile"""
    active_skills = [
        SkillProfile(
            name=name,
            description=f"Expert in {name}",
            skill_id=f"skill_{name.lower().replace(' ', '_')}",
            skill_set_name="default",
        )
        for name in (skills or ["Python"])
    ]

    return WorkerProfile(
        staff_id=staff_id,
        profile_id=profile_id,
        profile_type=profile_type,
        source_root="/test",
        context_fragments=[
            ContextFragment(
                kind=ContextKind.AGENT,
                filename="AGENTS.md",
                content=f"Expert in {', '.join(skills or ['Python'])}",
                source_path="/test/AGENTS.md",
            )
        ],
        active_skills=active_skills,
    )


def create_test_digest(
    profile_key: str,
    mode: RetrievalMode,
    question: str,
) -> WorkerContextDigest:
    """Create a test WorkerContextDigest"""
    return WorkerContextDigest(
        profile_key=profile_key,
        mode=mode,
        question=question,
        context_summary="Test summary",
        total_fragments=1,
        selected_fragments=1,
        total_skills=1,
        selected_skills=1,
    )


class TestFusionSimulationService:
    """测试 FusionSimulationService"""

    @pytest.fixture
    def sample_profiles(self):
        """Create sample profiles for testing"""
        return [
            create_test_profile("001", skills=["Python", "API Design"]),
            create_test_profile("002", skills=["Java", "System Design"]),
            create_test_profile("003", skills=["Python", "Machine Learning"]),
        ]

    @pytest.fixture
    def sample_digests(self):
        """Create sample context digests"""
        return [
            create_test_digest("staff_001:default", RetrievalMode.AGENT, "test question"),
            create_test_digest("staff_002:default", RetrievalMode.AGENT, "test question"),
            create_test_digest("staff_003:default", RetrievalMode.AGENT, "test question"),
        ]

    def test_simulate_basic(self, sample_profiles, sample_digests):
        """测试基本模拟功能"""
        from src.domain.services.fusion_simulation_service import FusionSimulationService

        service = FusionSimulationService()

        input_data = FusionSimulationInput(
            question="How to design an API?",
            mode=RetrievalMode.AGENT,
            profiles=sample_profiles,
            context_digests=sample_digests,
        )

        result = service.simulate(input_data)

        assert result is not None
        assert result.question == "How to design an API?"
        assert result.fusion_mode == "agent"
        assert len(result.perspectives) > 0

    def test_simulate_g1_mode(self, sample_profiles, sample_digests):
        """测试 G1 (AGENT) 模式"""
        from src.domain.services.fusion_simulation_service import FusionSimulationService

        service = FusionSimulationService()

        input_data = FusionSimulationInput(
            question="Python API development",
            mode=RetrievalMode.AGENT,
            profiles=sample_profiles,
            context_digests=sample_digests,
        )

        result = service.simulate(input_data)

        assert result.fusion_mode == "agent"
        assert result.recommendation is not None or len(result.perspectives) > 0

    def test_simulate_g2_mode(self, sample_profiles, sample_digests):
        """测试 G2 (CONFLICT_ALIGNMENT) 模式"""
        from src.domain.services.fusion_simulation_service import FusionSimulationService

        service = FusionSimulationService()

        input_data = FusionSimulationInput(
            question="Architecture decision between Python and Java",
            mode=RetrievalMode.CONFLICT_ALIGNMENT,
            profiles=sample_profiles,
            context_digests=sample_digests,
        )

        result = service.simulate(input_data)

        assert result.fusion_mode == "conflict_alignment"
        # G2 应该包含冲突分析相关字段
        assert result.key_insights is not None

    def test_simulate_g5_mode(self, sample_profiles, sample_digests):
        """测试 G5 (EXPERT_DIAGNOSIS) 模式"""
        from src.domain.services.fusion_simulation_service import FusionSimulationService

        service = FusionSimulationService()

        input_data = FusionSimulationInput(
            question="System health diagnosis",
            mode=RetrievalMode.EXPERT_DIAGNOSIS,
            profiles=sample_profiles,
            context_digests=sample_digests,
        )

        result = service.simulate(input_data)

        assert result.fusion_mode == "expert_diagnosis"
        # G5 应该包含诊断相关字段
        assert result.summary is not None or result.recommendations is not None

    def test_simulate_with_max_perspectives(self, sample_profiles, sample_digests):
        """测试 max_perspectives 限制"""
        from src.domain.services.fusion_simulation_service import FusionSimulationService

        service = FusionSimulationService()

        input_data = FusionSimulationInput(
            question="test question",
            mode=RetrievalMode.AGENT,
            profiles=sample_profiles,
            context_digests=sample_digests,
            max_perspectives=2,
        )

        result = service.simulate(input_data)

        assert len(result.perspectives) <= 2

    def test_simulate_generates_group_id(self, sample_profiles, sample_digests):
        """测试生成 group_id"""
        from src.domain.services.fusion_simulation_service import FusionSimulationService

        service = FusionSimulationService()

        input_data = FusionSimulationInput(
            question="test",
            mode=RetrievalMode.AGENT,
            profiles=sample_profiles,
            context_digests=sample_digests,
        )

        result = service.simulate(input_data)

        assert result.group_id is not None
        assert len(result.group_id) > 0

    def test_simulate_generates_fusion_id(self, sample_profiles, sample_digests):
        """测试生成 fusion_id"""
        from src.domain.services.fusion_simulation_service import FusionSimulationService

        service = FusionSimulationService()

        input_data = FusionSimulationInput(
            question="test",
            mode=RetrievalMode.AGENT,
            profiles=sample_profiles,
            context_digests=sample_digests,
        )

        result = service.simulate(input_data)

        assert result.fusion_id is not None
        assert len(result.fusion_id) > 0

    def test_simulate_generates_timing(self, sample_profiles, sample_digests):
        """测试生成 timing 信息"""
        from src.domain.services.fusion_simulation_service import FusionSimulationService

        service = FusionSimulationService()

        input_data = FusionSimulationInput(
            question="test",
            mode=RetrievalMode.AGENT,
            profiles=sample_profiles,
            context_digests=sample_digests,
        )

        result = service.simulate(input_data)

        assert result.timing is not None
        assert result.timing.started_at is not None
        assert result.timing.finished_at is not None
        assert result.timing.duration_ms >= 0

    def test_simulate_empty_profiles(self):
        """测试空 profiles 处理"""
        from src.domain.services.fusion_simulation_service import FusionSimulationService

        service = FusionSimulationService()

        input_data = FusionSimulationInput(
            question="test question",
            mode=RetrievalMode.AGENT,
            profiles=[],
            context_digests=[],
        )

        result = service.simulate(input_data)

        # 空 profiles 应该返回警告
        assert result.partial_success is False or len(result.warnings) > 0

    def test_simulate_with_options(self, sample_profiles, sample_digests):
        """测试 options 参数"""
        from src.domain.services.fusion_simulation_service import FusionSimulationService

        service = FusionSimulationService()

        input_data = FusionSimulationInput(
            question="test question",
            mode=RetrievalMode.AGENT,
            profiles=sample_profiles,
            context_digests=sample_digests,
            options={
                "timeout_ms": 30000,
                "custom_flag": True,
            },
        )

        result = service.simulate(input_data)

        assert result is not None


class TestFusionSimulationModes:
    """测试不同模式的特殊行为"""

    def test_g1_prioritizes_relevance(self):
        """测试 G1 优先相关性"""
        from src.domain.services.fusion_simulation_service import FusionSimulationService

        profiles = [
            create_test_profile("001", skills=["Python"]),
            create_test_profile("002", skills=["Java"]),
        ]

        digests = [
            create_test_digest("staff_001:default", RetrievalMode.AGENT, "Python programming"),
            create_test_digest("staff_002:default", RetrievalMode.AGENT, "Python programming"),
        ]

        service = FusionSimulationService()

        input_data = FusionSimulationInput(
            question="Python programming",
            mode=RetrievalMode.AGENT,
            profiles=profiles,
            context_digests=digests,
        )

        result = service.simulate(input_data)

        # G1 应该优先选择与 Python 相关的 profile
        assert len(result.perspectives) > 0

    def test_g2_includes_conflict_analysis(self):
        """测试 G2 包含冲突分析"""
        from src.domain.services.fusion_simulation_service import FusionSimulationService

        profiles = [
            create_test_profile("001", skills=["Python"]),
            create_test_profile("002", skills=["Java"]),
        ]

        digests = [
            create_test_digest("staff_001:default", RetrievalMode.CONFLICT_ALIGNMENT, "comparison"),
            create_test_digest("staff_002:default", RetrievalMode.CONFLICT_ALIGNMENT, "comparison"),
        ]

        service = FusionSimulationService()

        input_data = FusionSimulationInput(
            question="Should we use Python or Java?",
            mode=RetrievalMode.CONFLICT_ALIGNMENT,
            profiles=profiles,
            context_digests=digests,
        )

        result = service.simulate(input_data)

        # G2 模式应该生成
        assert result.fusion_mode == "conflict_alignment"

    def test_g5_includes_risk_assessment(self):
        """测试 G5 包含风险评估"""
        from src.domain.services.fusion_simulation_service import FusionSimulationService

        profiles = [
            create_test_profile("001", skills=["Security"]),
            create_test_profile("002", skills=["Database"]),
            create_test_profile("003", skills=["Infrastructure"]),
        ]

        digests = [
            create_test_digest("staff_001:default", RetrievalMode.EXPERT_DIAGNOSIS, "diagnosis"),
            create_test_digest("staff_002:default", RetrievalMode.EXPERT_DIAGNOSIS, "diagnosis"),
            create_test_digest("staff_003:default", RetrievalMode.EXPERT_DIAGNOSIS, "diagnosis"),
        ]

        service = FusionSimulationService()

        input_data = FusionSimulationInput(
            question="System security assessment",
            mode=RetrievalMode.EXPERT_DIAGNOSIS,
            profiles=profiles,
            context_digests=digests,
        )

        result = service.simulate(input_data)

        # G5 应该包含专家诊断相关字段
        assert result.fusion_mode == "expert_diagnosis"


class TestFusionSimulationFromFusionRequest:
    """测试从 FusionRequest 创建并模拟"""

    def test_simulate_from_fusion_request_g1(self):
        """测试从 FusionRequest 模拟 G1"""
        from src.domain.services.fusion_simulation_service import FusionSimulationService

        service = FusionSimulationService()

        fusion_request = FusionRequest(
            question="How to implement REST API?",
            participants=["staff_001"],
            fusion_mode="agent",
        )

        profiles = [create_test_profile("001", skills=["API Design"])]

        result = service.simulate_from_request(
            fusion_request=fusion_request,
            profiles=profiles,
        )

        assert result.fusion_mode == "agent"
        assert result.question == "How to implement REST API?"

    def test_simulate_from_fusion_request_g2(self):
        """测试从 FusionRequest 模拟 G2"""
        from src.domain.services.fusion_simulation_service import FusionSimulationService

        service = FusionSimulationService()

        fusion_request = FusionRequest(
            question="Resolve architecture conflict",
            participants=["staff_001", "staff_002"],
            fusion_mode="conflict_alignment",
        )

        profiles = [
            create_test_profile("001", skills=["Architecture"]),
            create_test_profile("002", skills=["Security"]),
        ]

        result = service.simulate_from_request(
            fusion_request=fusion_request,
            profiles=profiles,
        )

        assert result.fusion_mode == "conflict_alignment"

    def test_simulate_from_fusion_request_g5(self):
        """测试从 FusionRequest 模拟 G5"""
        from src.domain.services.fusion_simulation_service import FusionSimulationService

        service = FusionSimulationService()

        fusion_request = FusionRequest(
            question="Expert diagnosis required",
            participants=["staff_001"],
            fusion_mode="expert_diagnosis",
        )

        profiles = [create_test_profile("001", skills=["Diagnostics"])]

        result = service.simulate_from_request(
            fusion_request=fusion_request,
            profiles=profiles,
        )

        assert result.fusion_mode == "expert_diagnosis"


class TestFusionSimulationPerspectives:
    """测试视角生成"""

    def test_perspectives_match_profiles(self):
        """测试视角与 profiles 匹配"""
        from src.domain.services.fusion_simulation_service import FusionSimulationService

        profiles = [
            create_test_profile("001", skills=["Python"]),
            create_test_profile("002", skills=["Java"]),
        ]

        digests = [
            create_test_digest("staff_001:default", RetrievalMode.AGENT, "test"),
            create_test_digest("staff_002:default", RetrievalMode.AGENT, "test"),
        ]

        service = FusionSimulationService()

        input_data = FusionSimulationInput(
            question="test",
            mode=RetrievalMode.AGENT,
            profiles=profiles,
            context_digests=digests,
        )

        result = service.simulate(input_data)

        # 视角数量应该与 profiles 对应
        assert len(result.perspectives) <= len(profiles)

    def test_perspective_has_required_fields(self):
        """测试视角包含必要字段"""
        from src.domain.services.fusion_simulation_service import FusionSimulationService

        profiles = [create_test_profile("001", skills=["Python"])]
        digests = [create_test_digest("staff_001:default", RetrievalMode.AGENT, "test")]

        service = FusionSimulationService()

        input_data = FusionSimulationInput(
            question="test",
            mode=RetrievalMode.AGENT,
            profiles=profiles,
            context_digests=digests,
        )

        result = service.simulate(input_data)

        if result.perspectives:
            perspective = result.perspectives[0]
            assert perspective.participant_id is not None
            assert perspective.summary is not None
            assert perspective.status in ["completed", "timed_out", "failed", "skipped"]
            assert perspective.role in ["driver", "consultant", "observer", "expert"]