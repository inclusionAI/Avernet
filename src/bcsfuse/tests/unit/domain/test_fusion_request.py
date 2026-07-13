"""
Tests for FusionRequest domain models

G1: Fusion Entry Layer

测试 FusionRequest, FuseOptions, FuseMetadata 的模型定义。
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError


class TestFusionRequestModule:
    """模块存在性测试"""

    def test_module_exists(self):
        """测试 fusion_request 模块存在"""
        import importlib

        module = importlib.import_module("src.domain.models.fusion_request")
        assert module is not None

    def test_fusion_request_class_exists(self):
        """测试 FusionRequest 类存在"""
        from src.domain.models.fusion_request import FusionRequest

        assert FusionRequest is not None


class TestFusionRequestBasic:
    """FusionRequest 基本测试"""

    def test_create_minimal_request(self):
        """测试创建最小请求"""
        from src.domain.models.fusion_request import FusionRequest

        request = FusionRequest(
            question="这个方案是否可行",
            participants=["zhangsan", "dba"],
        )

        assert request.question == "这个方案是否可行"
        assert request.participants == ["zhangsan", "dba"]
        assert request.driver_bot_id is None
        assert request.mode == "agent"

    def test_create_full_request(self):
        """测试创建完整请求"""
        from src.domain.models.fusion_request import (
            FusionRequest,
            FuseOptions,
            FuseMetadata,
        )

        options = FuseOptions(
            timeout_ms=30000,
            parallel=False,
            include_recommendation=True,
            strict_participants=False,
        )

        metadata = FuseMetadata(
            request_id="req-001",
            source="bcs-cli",
            operator="admin",
        )

        request = FusionRequest(
            question="这个方案从各角度是否可行",
            participants=["zhangsan", "dba", "security"],
            driver_bot_id="zhangsan",
            mode="agent",
            options=options,
            metadata=metadata,
        )

        assert request.question == "这个方案从各角度是否可行"
        assert request.participants == ["zhangsan", "dba", "security"]
        assert request.driver_bot_id == "zhangsan"
        assert request.options.timeout_ms == 30000
        assert request.metadata.request_id == "req-001"

    def test_request_requires_question(self):
        """测试 question 是必填字段"""
        from src.domain.models.fusion_request import FusionRequest

        with pytest.raises(ValidationError):
            FusionRequest(participants=["zhangsan"])

    def test_request_requires_participants(self):
        """测试 participants 是必填字段"""
        from src.domain.models.fusion_request import FusionRequest

        with pytest.raises(ValidationError):
            FusionRequest(question="测试问题")

    def test_request_participants_min_length(self):
        """测试 participants 至少 1 个"""
        from src.domain.models.fusion_request import FusionRequest

        with pytest.raises(ValidationError):
            FusionRequest(question="测试问题", participants=[])

    def test_request_participants_max_length(self):
        """测试 participants 最多 20 个"""
        from src.domain.models.fusion_request import FusionRequest

        too_many = [f"participant_{i}" for i in range(21)]

        with pytest.raises(ValidationError):
            FusionRequest(question="测试问题", participants=too_many)

    def test_request_question_max_length(self):
        """测试 question 最大长度 2000"""
        from src.domain.models.fusion_request import FusionRequest

        long_question = "x" * 2001

        with pytest.raises(ValidationError):
            FusionRequest(question=long_question, participants=["zhangsan"])


class TestFuseOptions:
    """FuseOptions 测试"""

    def test_default_options(self):
        """测试默认选项"""
        from src.domain.models.fusion_request import FuseOptions

        options = FuseOptions()

        assert options.timeout_ms == 15000
        assert options.parallel is True
        assert options.include_recommendation is True
        assert options.include_transcript is False
        assert options.strict_participants is True
        assert options.fail_fast is False

    def test_custom_options(self):
        """测试自定义选项"""
        from src.domain.models.fusion_request import FuseOptions

        options = FuseOptions(
            timeout_ms=60000,
            parallel=False,
            include_recommendation=False,
            strict_participants=False,
        )

        assert options.timeout_ms == 60000
        assert options.parallel is False
        assert options.include_recommendation is False
        assert options.strict_participants is False

    def test_timeout_ms_minimum(self):
        """测试 timeout_ms 最小值"""
        from src.domain.models.fusion_request import FuseOptions

        with pytest.raises(ValidationError):
            FuseOptions(timeout_ms=500)  # 最小 1000

    def test_timeout_ms_maximum(self):
        """测试 timeout_ms 最大值（FuseOptions 层不限，由 FusionRequest 动态校验）"""
        from src.domain.models.fusion_request import FuseOptions

        # FuseOptions 层现在允许最大 300000ms，实际校验在 FusionRequest 层
        options = FuseOptions(timeout_ms=150000)  # 在新的范围内
        assert options.timeout_ms == 150000

        # 超过 300000ms 仍然无效
        with pytest.raises(ValidationError):
            FuseOptions(timeout_ms=350000)  # 超过最大值 300000


class TestFuseMetadata:
    """FuseMetadata 测试"""

    def test_empty_metadata(self):
        """测试空 metadata"""
        from src.domain.models.fusion_request import FuseMetadata

        metadata = FuseMetadata()

        assert metadata.request_id is None
        assert metadata.source is None
        assert metadata.operator is None
        assert metadata.trace_id is None

    def test_full_metadata(self):
        """测试完整 metadata"""
        from src.domain.models.fusion_request import FuseMetadata

        metadata = FuseMetadata(
            request_id="req-001",
            source="bcs-cli",
            operator="admin",
            trace_id="trace-abc123",
        )

        assert metadata.request_id == "req-001"
        assert metadata.source == "bcs-cli"
        assert metadata.operator == "admin"
        assert metadata.trace_id == "trace-abc123"


class TestFusionRequestValidation:
    """FusionRequest 验证测试"""

    def test_mode_must_be_agent(self):
        """测试 mode 必须是 agent（G1 向后兼容）"""
        from src.domain.models.fusion_request import FusionRequest

        # 默认 mode 是 agent
        request = FusionRequest(
            question="测试问题",
            participants=["zhangsan"],
        )
        assert request.mode == "agent"

        # 其他 mode 应该失败
        with pytest.raises(ValidationError):
            FusionRequest(
                question="测试问题",
                participants=["zhangsan"],
                mode="invalid_mode",
            )

    def test_driver_bot_id_optional(self):
        """测试 driver_bot_id 可选"""
        from src.domain.models.fusion_request import FusionRequest

        # 不指定 driver_bot_id
        request = FusionRequest(
            question="测试问题",
            participants=["zhangsan", "dba"],
        )
        assert request.driver_bot_id is None

        # 指定 driver_bot_id
        request = FusionRequest(
            question="测试问题",
            participants=["zhangsan", "dba"],
            driver_bot_id="zhangsan",
        )
        assert request.driver_bot_id == "zhangsan"

    def test_extra_fields_forbidden(self):
        """测试额外字段被禁止"""
        from src.domain.models.fusion_request import FusionRequest

        with pytest.raises(ValidationError):
            FusionRequest(
                question="测试问题",
                participants=["zhangsan"],
                unknown_field="should_fail",  # type: ignore
            )

    def test_timeout_ms_dynamic_validation_g1_g2(self):
        """测试 G1/G2 模式 timeout_ms 最大 120000ms"""
        from src.domain.models.fusion_request import FusionRequest, FuseOptions

        # G1 模式下超过 120000ms 应该失败
        with pytest.raises(ValidationError):
            FusionRequest(
                question="测试问题",
                participants=["zhangsan"],
                fusion_mode="agent",
                options=FuseOptions(timeout_ms=150000),
            )

        # G2 模式下超过 120000ms 应该失败
        with pytest.raises(ValidationError):
            FusionRequest(
                question="测试问题",
                participants=["zhangsan"],
                fusion_mode="conflict_alignment",
                options=FuseOptions(timeout_ms=150000),
            )

        # G1/G2 模式下 120000ms 应该成功
        request = FusionRequest(
            question="测试问题",
            participants=["zhangsan"],
            fusion_mode="agent",
            options=FuseOptions(timeout_ms=120000),
        )
        assert request.options.timeout_ms == 120000

    def test_timeout_ms_dynamic_validation_g5(self):
        """测试 G5 expert_diagnosis 模式 timeout_ms 最大 300000ms"""
        from src.domain.models.fusion_request import FusionRequest, FuseOptions

        # G5 模式下 180000ms 应该成功
        request = FusionRequest(
            question="测试问题",
            participants=["zhangsan"],
            fusion_mode="expert_diagnosis",
            options=FuseOptions(timeout_ms=180000),
        )
        assert request.options.timeout_ms == 180000

        # G5 模式下 300000ms 应该成功
        request = FusionRequest(
            question="测试问题",
            participants=["zhangsan"],
            fusion_mode="expert_diagnosis",
            options=FuseOptions(timeout_ms=300000),
        )
        assert request.options.timeout_ms == 300000

        # G5 模式下超过 300000ms 应该失败
        with pytest.raises(ValidationError):
            FusionRequest(
                question="测试问题",
                participants=["zhangsan"],
                fusion_mode="expert_diagnosis",
                options=FuseOptions(timeout_ms=350000),
            )


# =============================================================================
# G2: Conflict Alignment Tests
# =============================================================================


class TestFusionRequestG2Mode:
    """FusionRequest G2 模式测试"""

    def test_fusion_mode_default_is_agent(self):
        """测试 fusion_mode 默认是 agent（向后兼容）"""
        from src.domain.models.fusion_request import FusionRequest

        request = FusionRequest(
            question="测试问题",
            participants=["zhangsan"],
        )

        # 默认应该是 agent（G1）
        assert request.fusion_mode == "agent"

    def test_fusion_mode_conflict_alignment_accepted(self):
        """测试 fusion_mode 接受 conflict_alignment（G2）"""
        from src.domain.models.fusion_request import FusionRequest

        request = FusionRequest(
            question="如何协调代码与PRD的超时时间冲突？",
            participants=["zhangsan", "lisi", "anquan"],
            fusion_mode="conflict_alignment",
        )

        assert request.fusion_mode == "conflict_alignment"

    def test_fusion_mode_agent_explicit(self):
        """测试显式设置 fusion_mode 为 agent"""
        from src.domain.models.fusion_request import FusionRequest

        request = FusionRequest(
            question="测试问题",
            participants=["zhangsan"],
            fusion_mode="agent",
        )

        assert request.fusion_mode == "agent"

    def test_fusion_mode_invalid_rejected(self):
        """测试无效 fusion_mode 被拒绝"""
        from src.domain.models.fusion_request import FusionRequest

        with pytest.raises(ValidationError):
            FusionRequest(
                question="测试问题",
                participants=["zhangsan"],
                fusion_mode="invalid_mode",  # type: ignore
            )

    def test_mode_and_fusion_mode_coexist(self):
        """测试 mode 和 fusion_mode 可以共存（向后兼容过渡）"""
        from src.domain.models.fusion_request import FusionRequest

        # G1 请求：只用 mode，不指定 fusion_mode
        g1_request = FusionRequest(
            question="G1 test",
            participants=["zhangsan"],
        )
        assert g1_request.mode == "agent"
        assert g1_request.fusion_mode == "agent"  # 默认

        # G2 请求：使用 fusion_mode
        g2_request = FusionRequest(
            question="G2 test",
            participants=["zhangsan", "lisi"],
            fusion_mode="conflict_alignment",
        )
        assert g2_request.mode == "agent"  # 原有字段不变
        assert g2_request.fusion_mode == "conflict_alignment"


class TestFuseOptionsG2:
    """FuseOptions G2 选项测试"""

    def test_detect_conflicts_option(self):
        """测试 detect_conflicts 选项"""
        from src.domain.models.fusion_request import FuseOptions

        options = FuseOptions(detect_conflicts=True)
        assert options.detect_conflicts is True

    def test_extract_alignment_points_option(self):
        """测试 extract_alignment_points 选项"""
        from src.domain.models.fusion_request import FuseOptions

        options = FuseOptions(extract_alignment_points=True)
        assert options.extract_alignment_points is True

    def test_g2_options_defaults(self):
        """测试 G2 选项默认值"""
        from src.domain.models.fusion_request import FuseOptions

        options = FuseOptions()

        # G2 选项默认值
        assert options.detect_conflicts is False
        assert options.extract_alignment_points is False

    def test_g2_options_all_enabled(self):
        """测试 G2 选项全部启用"""
        from src.domain.models.fusion_request import FuseOptions

        options = FuseOptions(
            detect_conflicts=True,
            extract_alignment_points=True,
        )

        assert options.detect_conflicts is True
        assert options.extract_alignment_points is True


# =============================================================================
# G5: Expert Diagnosis Tests
# =============================================================================


class TestFusionRequestG5Mode:
    """FusionRequest G5 模式测试"""

    def test_fusion_mode_expert_diagnosis_accepted(self):
        """测试 fusion_mode 接受 expert_diagnosis（G5）"""
        from src.domain.models.fusion_request import FusionRequest

        request = FusionRequest(
            question="这个方案是否可以上线？",
            participants=["anquan", "fawu", "dba"],
            fusion_mode="expert_diagnosis",
        )

        assert request.fusion_mode == "expert_diagnosis"

    def test_fusion_mode_all_modes(self):
        """测试所有 fusion_mode 有效值"""
        from src.domain.models.fusion_request import FusionRequest

        for mode in ["agent", "conflict_alignment", "expert_diagnosis"]:
            request = FusionRequest(
                question="test",
                participants=["zhangsan"],
                fusion_mode=mode,  # type: ignore
            )
            assert request.fusion_mode == mode

    def test_g5_request_with_driver(self):
        """测试 G5 请求包含 driver_bot_id"""
        from src.domain.models.fusion_request import FusionRequest

        request = FusionRequest(
            question="上线评审",
            participants=["anquan", "fawu", "dba"],
            driver_bot_id="dba",
            fusion_mode="expert_diagnosis",
        )

        assert request.fusion_mode == "expert_diagnosis"
        assert request.driver_bot_id == "dba"

    def test_g5_request_with_options(self):
        """测试 G5 请求包含选项"""
        from src.domain.models.fusion_request import FusionRequest, FuseOptions

        options = FuseOptions(
            timeout_ms=60000,
            include_recommendation=True,
        )

        request = FusionRequest(
            question="上线评审",
            participants=["anquan", "fawu"],
            fusion_mode="expert_diagnosis",
            options=options,
        )

        assert request.fusion_mode == "expert_diagnosis"
        assert request.options.timeout_ms == 60000


class TestFuseOptionsG5:
    """FuseOptions G5 选项测试"""

    def test_enable_risk_assessment_option(self):
        """测试 enable_risk_assessment 选项"""
        from src.domain.models.fusion_request import FuseOptions

        options = FuseOptions(enable_risk_assessment=True)
        assert options.enable_risk_assessment is True

        options_disabled = FuseOptions(enable_risk_assessment=False)
        assert options_disabled.enable_risk_assessment is False

    def test_enable_expert_recommendations_option(self):
        """测试 enable_expert_recommendations 选项"""
        from src.domain.models.fusion_request import FuseOptions

        options = FuseOptions(enable_expert_recommendations=True)
        assert options.enable_expert_recommendations is True

        options_disabled = FuseOptions(enable_expert_recommendations=False)
        assert options_disabled.enable_expert_recommendations is False

    def test_enable_go_live_conditions_option(self):
        """测试 enable_go_live_conditions 选项"""
        from src.domain.models.fusion_request import FuseOptions

        options = FuseOptions(enable_go_live_conditions=True)
        assert options.enable_go_live_conditions is True

        options_disabled = FuseOptions(enable_go_live_conditions=False)
        assert options_disabled.enable_go_live_conditions is False

    def test_g5_options_defaults(self):
        """测试 G5 选项默认值"""
        from src.domain.models.fusion_request import FuseOptions

        options = FuseOptions()

        # G5 选项默认值（默认启用）
        assert options.enable_risk_assessment is True
        assert options.enable_expert_recommendations is True
        assert options.enable_go_live_conditions is True

    def test_g5_options_all_enabled(self):
        """测试 G5 选项全部启用"""
        from src.domain.models.fusion_request import FuseOptions

        options = FuseOptions(
            enable_risk_assessment=True,
            enable_expert_recommendations=True,
            enable_go_live_conditions=True,
        )

        assert options.enable_risk_assessment is True
        assert options.enable_expert_recommendations is True
        assert options.enable_go_live_conditions is True

    def test_g5_options_all_disabled(self):
        """测试 G5 选项全部禁用"""
        from src.domain.models.fusion_request import FuseOptions

        options = FuseOptions(
            enable_risk_assessment=False,
            enable_expert_recommendations=False,
            enable_go_live_conditions=False,
        )

        assert options.enable_risk_assessment is False
        assert options.enable_expert_recommendations is False
        assert options.enable_go_live_conditions is False

    def test_g5_options_with_g2_options(self):
        """测试 G5 选项与 G2 选项可以共存"""
        from src.domain.models.fusion_request import FuseOptions

        options = FuseOptions(
            detect_conflicts=True,
            extract_alignment_points=True,
            enable_risk_assessment=True,
            enable_expert_recommendations=True,
            enable_go_live_conditions=True,
        )

        # G2 选项
        assert options.detect_conflicts is True
        assert options.extract_alignment_points is True
        # G5 选项
        assert options.enable_risk_assessment is True
        assert options.enable_expert_recommendations is True
        assert options.enable_go_live_conditions is True