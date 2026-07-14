"""
ModelProfile 领域模型测试

测试模型档案模型的验证和行为。
"""

import pytest
from pydantic import ValidationError

from src.domain.models.model_profile import (
    ModelProfile,
    ModelTier,
    CostClass,
    LatencyClass,
)
from src.domain.models.llm_task_spec import TaskType


class TestModelTier:
    """ModelTier 枚举测试"""

    def test_tier_values(self):
        """测试层级枚举值"""
        assert ModelTier.FAST == "fast"
        assert ModelTier.BALANCED == "balanced"
        assert ModelTier.REASONING == "reasoning"
        assert ModelTier.LONG_CONTEXT == "long_context"
        assert ModelTier.EXTRACTION == "extraction"


class TestCostClass:
    """CostClass 枚举测试"""

    def test_cost_class_values(self):
        """测试成本类枚举值"""
        assert CostClass.LOW == "low"
        assert CostClass.MEDIUM == "medium"
        assert CostClass.HIGH == "high"


class TestLatencyClass:
    """LatencyClass 枚举测试"""

    def test_latency_class_values(self):
        """测试延迟类枚举值"""
        assert LatencyClass.LOW == "low"
        assert LatencyClass.MEDIUM == "medium"
        assert LatencyClass.HIGH == "high"


class TestModelProfile:
    """ModelProfile 模型测试"""

    def test_create_minimal_profile(self):
        """测试创建最小模型档案"""
        profile = ModelProfile(
            logical_model_id="fast.default",
            provider_id="anthropic",
            physical_model_name="claude-3-haiku",
            tier=ModelTier.FAST,
        )

        assert profile.logical_model_id == "fast.default"
        assert profile.provider_id == "anthropic"
        assert profile.physical_model_name == "claude-3-haiku"
        assert profile.tier == ModelTier.FAST
        assert profile.supports_json is False  # 默认值
        assert profile.supports_long_context is False  # 默认值
        assert profile.cost_class == CostClass.MEDIUM  # 默认值
        assert profile.latency_class == LatencyClass.MEDIUM  # 默认值
        assert profile.recommended_for == []  # 默认值

    def test_create_full_profile(self):
        """测试创建完整模型档案"""
        profile = ModelProfile(
            logical_model_id="reasoning.default",
            provider_id="anthropic",
            physical_model_name="claude-3-opus",
            tier=ModelTier.REASONING,
            supports_json=True,
            supports_long_context=True,
            cost_class=CostClass.HIGH,
            latency_class=LatencyClass.HIGH,
            recommended_for=[TaskType.FUSION_RECOMMENDATION, TaskType.PLANNING],
        )

        assert profile.logical_model_id == "reasoning.default"
        assert profile.provider_id == "anthropic"
        assert profile.physical_model_name == "claude-3-opus"
        assert profile.tier == ModelTier.REASONING
        assert profile.supports_json is True
        assert profile.supports_long_context is True
        assert profile.cost_class == CostClass.HIGH
        assert profile.latency_class == LatencyClass.HIGH
        assert TaskType.FUSION_RECOMMENDATION in profile.recommended_for
        assert TaskType.PLANNING in profile.recommended_for

    def test_required_fields(self):
        """测试必填字段"""
        with pytest.raises(ValidationError) as exc_info:
            ModelProfile()

        errors = exc_info.value.errors()
        error_fields = {e["loc"][0] for e in errors}
        assert "logical_model_id" in error_fields
        assert "provider_id" in error_fields
        assert "physical_model_name" in error_fields
        assert "tier" in error_fields

    def test_logical_model_id_pattern(self):
        """测试逻辑模型 ID 格式"""
        # 有效格式
        profile = ModelProfile(
            logical_model_id="fast.default",
            provider_id="test",
            physical_model_name="test-model",
            tier=ModelTier.FAST,
        )
        assert profile.logical_model_id == "fast.default"

        profile = ModelProfile(
            logical_model_id="reasoning.v1",
            provider_id="test",
            physical_model_name="test-model",
            tier=ModelTier.REASONING,
        )
        assert profile.logical_model_id == "reasoning.v1"

    def test_extra_fields_forbidden(self):
        """测试禁止额外字段"""
        with pytest.raises(ValidationError):
            ModelProfile(
                logical_model_id="fast.default",
                provider_id="test",
                physical_model_name="test-model",
                tier=ModelTier.FAST,
                unknown_field="value",  # type: ignore
            )

    def test_model_dump(self):
        """测试模型序列化"""
        profile = ModelProfile(
            logical_model_id="fast.default",
            provider_id="anthropic",
            physical_model_name="claude-3-haiku",
            tier=ModelTier.FAST,
            supports_json=True,
        )

        data = profile.model_dump()

        assert data["logical_model_id"] == "fast.default"
        assert data["provider_id"] == "anthropic"
        assert data["physical_model_name"] == "claude-3-haiku"
        assert data["tier"] == "fast"
        assert data["supports_json"] is True

    def test_recommended_for_task_type_conversion(self):
        """测试 recommended_for 任务类型自动转换"""
        profile = ModelProfile(
            logical_model_id="fast.default",
            provider_id="test",
            physical_model_name="test-model",
            tier=ModelTier.FAST,
            recommended_for=["fusion_recommendation", "summary"],  # type: ignore
        )

        assert profile.recommended_for[0] == TaskType.FUSION_RECOMMENDATION
        assert profile.recommended_for[1] == TaskType.SUMMARY