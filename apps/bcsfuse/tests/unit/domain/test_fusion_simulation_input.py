"""
Tests for Fusion Simulation Input

Worker Profile Retrieval & Fusion Simulation Baseline

测试范围：
- FusionSimulationInput: Fusion Simulation 输入模型
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError


class TestFusionSimulationInput:
    """测试 FusionSimulationInput 模型"""

    def test_create_simulation_input_success(self):
        """测试创建模拟输入"""
        from src.domain.models.fusion_simulation_input import FusionSimulationInput
        from src.domain.models.retrieval_mode import RetrievalMode
        from src.domain.models.worker_profile import WorkerProfile, ProfileType

        profile = WorkerProfile(
            staff_id="001",
            profile_id="default",
            profile_type=ProfileType.DEFAULT,
            source_root="/test",
        )

        simulation_input = FusionSimulationInput(
            question="How to implement web search?",
            mode=RetrievalMode.AGENT,
            profiles=[profile],
        )

        assert simulation_input.question == "How to implement web search?"
        assert simulation_input.mode == RetrievalMode.AGENT
        assert len(simulation_input.profiles) == 1
        assert simulation_input.max_perspectives == 3  # 默认值

    def test_create_simulation_input_with_all_fields(self):
        """测试创建包含所有字段的模拟输入"""
        from src.domain.models.fusion_simulation_input import FusionSimulationInput
        from src.domain.models.retrieval_mode import RetrievalMode
        from src.domain.models.worker_profile import WorkerProfile, ProfileType
        from src.domain.models.worker_context_digest import WorkerContextDigest

        profile = WorkerProfile(
            staff_id="001",
            profile_id="default",
            profile_type=ProfileType.DEFAULT,
            source_root="/test",
        )

        digest = WorkerContextDigest(
            profile_key="staff_001:default",
            mode=RetrievalMode.AGENT,
            question="test",
            context_summary="test summary",
        )

        simulation_input = FusionSimulationInput(
            question="test question",
            mode=RetrievalMode.EXPERT_DIAGNOSIS,
            profiles=[profile],
            context_digests=[digest],
            max_perspectives=5,
            options={
                "include_risk_assessment": True,
                "domain_diversity": 0.8,
            },
        )

        assert simulation_input.max_perspectives == 5
        assert len(simulation_input.context_digests) == 1
        assert simulation_input.options["include_risk_assessment"] is True

    def test_create_simulation_input_minimal(self):
        """测试创建最小字段模拟输入"""
        from src.domain.models.fusion_simulation_input import FusionSimulationInput
        from src.domain.models.retrieval_mode import RetrievalMode

        simulation_input = FusionSimulationInput(
            question="test",
            mode=RetrievalMode.GENERAL,
        )

        assert simulation_input.question == "test"
        assert simulation_input.profiles == []
        assert simulation_input.context_digests == []
        assert simulation_input.max_perspectives == 3
        assert simulation_input.options == {}

    def test_max_perspectives_validation(self):
        """测试 max_perspectives 验证"""
        from src.domain.models.fusion_simulation_input import FusionSimulationInput
        from src.domain.models.retrieval_mode import RetrievalMode

        # 有效值
        for value in [1, 3, 5, 10]:
            simulation_input = FusionSimulationInput(
                question="test",
                mode=RetrievalMode.AGENT,
                max_perspectives=value,
            )
            assert simulation_input.max_perspectives == value

        # 无效值
        with pytest.raises(ValidationError):
            FusionSimulationInput(
                question="test",
                mode=RetrievalMode.AGENT,
                max_perspectives=0,
            )

        with pytest.raises(ValidationError):
            FusionSimulationInput(
                question="test",
                mode=RetrievalMode.AGENT,
                max_perspectives=-1,
            )

    def test_mode_different_values(self):
        """测试不同模式"""
        from src.domain.models.fusion_simulation_input import FusionSimulationInput
        from src.domain.models.retrieval_mode import RetrievalMode

        for mode in [RetrievalMode.AGENT, RetrievalMode.CONFLICT_ALIGNMENT,
                     RetrievalMode.EXPERT_DIAGNOSIS, RetrievalMode.GENERAL]:
            simulation_input = FusionSimulationInput(
                question="test",
                mode=mode,
            )
            assert simulation_input.mode == mode

    def test_missing_required_fields_raises_error(self):
        """测试缺少必填字段抛出错误"""
        from src.domain.models.fusion_simulation_input import FusionSimulationInput

        with pytest.raises(ValidationError):
            FusionSimulationInput(
                mode="agent",
            )

        with pytest.raises(ValidationError):
            FusionSimulationInput(
                question="test",
            )

    def test_extra_fields_forbidden(self):
        """测试额外字段被禁止"""
        from src.domain.models.fusion_simulation_input import FusionSimulationInput
        from src.domain.models.retrieval_mode import RetrievalMode

        with pytest.raises(ValidationError):
            FusionSimulationInput(
                question="test",
                mode=RetrievalMode.AGENT,
                extra_field="not_allowed",  # type: ignore
            )

    def test_profiles_list(self):
        """测试 profiles 列表"""
        from src.domain.models.fusion_simulation_input import FusionSimulationInput
        from src.domain.models.retrieval_mode import RetrievalMode
        from src.domain.models.worker_profile import WorkerProfile, ProfileType

        profiles = [
            WorkerProfile(
                staff_id=str(i),
                profile_id="default",
                profile_type=ProfileType.DEFAULT,
                source_root="/test",
            )
            for i in range(3)
        ]

        simulation_input = FusionSimulationInput(
            question="test",
            mode=RetrievalMode.EXPERT_DIAGNOSIS,
            profiles=profiles,
        )

        assert len(simulation_input.profiles) == 3
        assert simulation_input.profile_count == 3

    def test_options_dict(self):
        """测试 options 字典"""
        from src.domain.models.fusion_simulation_input import FusionSimulationInput
        from src.domain.models.retrieval_mode import RetrievalMode

        simulation_input = FusionSimulationInput(
            question="test",
            mode=RetrievalMode.CONFLICT_ALIGNMENT,
            options={
                "include_conflicts": True,
                "alignment_threshold": 0.7,
                "max_conflicts": 5,
                "tags": ["security", "privacy"],
            },
        )

        assert simulation_input.options["include_conflicts"] is True
        assert simulation_input.options["alignment_threshold"] == 0.7
        assert len(simulation_input.options["tags"]) == 2


class TestFusionSimulationInputCompatibility:
    """测试与 FusionRequest 的兼容性"""

    def test_from_fusion_request(self):
        """测试从 FusionRequest 转换"""
        from src.domain.models.fusion_simulation_input import FusionSimulationInput
        from src.domain.models.fusion_request import FusionRequest
        from src.domain.models.retrieval_mode import RetrievalMode

        fusion_request = FusionRequest(
            question="How to optimize database performance?",
            participants=["staff_001", "staff_002"],
            fusion_mode="expert_diagnosis",
        )

        simulation_input = FusionSimulationInput.from_fusion_request(
            fusion_request,
            max_perspectives=4,
        )

        assert simulation_input.question == fusion_request.question
        assert simulation_input.mode == RetrievalMode.EXPERT_DIAGNOSIS
        assert simulation_input.max_perspectives == 4
        assert simulation_input.options["participants"] == ["staff_001", "staff_002"]

    def test_from_fusion_request_all_modes(self):
        """测试从 FusionRequest 转换所有模式"""
        from src.domain.models.fusion_simulation_input import FusionSimulationInput
        from src.domain.models.fusion_request import FusionRequest
        from src.domain.models.retrieval_mode import RetrievalMode

        for fusion_mode in ["agent", "conflict_alignment", "expert_diagnosis"]:
            fusion_request = FusionRequest(
                question="test",
                participants=["staff_001"],
                fusion_mode=fusion_mode,
            )

            simulation_input = FusionSimulationInput.from_fusion_request(fusion_request)
            assert simulation_input.mode.value == fusion_mode

    def test_from_fusion_request_with_profiles(self):
        """测试从 FusionRequest 转换并附带 profiles"""
        from src.domain.models.fusion_simulation_input import FusionSimulationInput
        from src.domain.models.fusion_request import FusionRequest
        from src.domain.models.retrieval_mode import RetrievalMode
        from src.domain.models.worker_profile import WorkerProfile, ProfileType

        fusion_request = FusionRequest(
            question="test question",
            participants=["staff_001"],
            fusion_mode="conflict_alignment",
        )

        profiles = [
            WorkerProfile(
                staff_id="001",
                profile_id="default",
                profile_type=ProfileType.DEFAULT,
                source_root="/test",
            ),
        ]

        simulation_input = FusionSimulationInput.from_fusion_request(
            fusion_request,
            profiles=profiles,
        )

        assert len(simulation_input.profiles) == 1
        assert simulation_input.mode == RetrievalMode.CONFLICT_ALIGNMENT

    def test_from_fusion_request_extracts_options(self):
        """测试从 FusionRequest 提取选项"""
        from src.domain.models.fusion_simulation_input import FusionSimulationInput
        from src.domain.models.fusion_request import FusionRequest, FuseOptions

        fusion_request = FusionRequest(
            question="test question",
            participants=["staff_001"],
            fusion_mode="agent",
            options=FuseOptions(
                timeout_ms=30000,
                parallel=False,
                detect_conflicts=True,
            ),
        )

        simulation_input = FusionSimulationInput.from_fusion_request(fusion_request)

        assert simulation_input.options["timeout_ms"] == 30000
        assert simulation_input.options["parallel"] is False
        assert simulation_input.options["detect_conflicts"] is True