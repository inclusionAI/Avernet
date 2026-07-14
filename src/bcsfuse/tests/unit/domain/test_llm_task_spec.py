"""
LLMTaskSpec 领域模型测试

测试 LLM 任务规格模型的验证和行为。
"""

import pytest
from pydantic import ValidationError

from src.domain.models.llm_task_spec import (
    LLMTaskSpec,
    TaskType,
    Complexity,
    CostSensitivity,
)


class TestTaskType:
    """TaskType 枚举测试"""

    def test_task_type_values(self):
        """测试枚举值完整"""
        assert TaskType.FUSION_RECOMMENDATION == "fusion_recommendation"
        assert TaskType.TASK_UNDERSTANDING == "task_understanding"
        assert TaskType.PLANNING == "planning"
        assert TaskType.EXTRACTION == "extraction"
        assert TaskType.SUMMARY == "summary"
        assert TaskType.RATIONALE_GENERATION == "rationale_generation"

    def test_task_type_from_string(self):
        """测试从字符串创建枚举"""
        tt = TaskType("fusion_recommendation")
        assert tt == TaskType.FUSION_RECOMMENDATION


class TestComplexity:
    """Complexity 枚举测试"""

    def test_complexity_values(self):
        """测试复杂度枚举值"""
        assert Complexity.LOW == "low"
        assert Complexity.MEDIUM == "medium"
        assert Complexity.HIGH == "high"


class TestCostSensitivity:
    """CostSensitivity 枚举测试"""

    def test_cost_sensitivity_values(self):
        """测试成本敏感度枚举值"""
        assert CostSensitivity.LOW == "low"
        assert CostSensitivity.MEDIUM == "medium"
        assert CostSensitivity.HIGH == "high"


class TestLLMTaskSpec:
    """LLMTaskSpec 模型测试"""

    def test_create_minimal_task_spec(self):
        """测试创建最小任务规格"""
        spec = LLMTaskSpec(task_type=TaskType.FUSION_RECOMMENDATION)

        assert spec.task_type == TaskType.FUSION_RECOMMENDATION
        assert spec.complexity == Complexity.MEDIUM  # 默认值
        assert spec.need_structured_output is False  # 默认值
        assert spec.context_size == 0  # 默认值
        assert spec.cost_sensitivity == CostSensitivity.MEDIUM  # 默认值
        assert spec.require_explanation is False  # 默认值
        assert spec.latency_budget_ms == 15000  # 默认值

    def test_create_full_task_spec(self):
        """测试创建完整任务规格"""
        spec = LLMTaskSpec(
            task_type=TaskType.FUSION_RECOMMENDATION,
            complexity=Complexity.HIGH,
            need_structured_output=True,
            context_size=5000,
            cost_sensitivity=CostSensitivity.LOW,
            require_explanation=True,
            latency_budget_ms=30000,
        )

        assert spec.task_type == TaskType.FUSION_RECOMMENDATION
        assert spec.complexity == Complexity.HIGH
        assert spec.need_structured_output is True
        assert spec.context_size == 5000
        assert spec.cost_sensitivity == CostSensitivity.LOW
        assert spec.require_explanation is True
        assert spec.latency_budget_ms == 30000

    def test_task_type_required(self):
        """测试 task_type 是必填字段"""
        with pytest.raises(ValidationError) as exc_info:
            LLMTaskSpec()

        errors = exc_info.value.errors()
        assert any(e["loc"] == ("task_type",) for e in errors)

    def test_latency_budget_validation(self):
        """测试 latency_budget 范围验证"""
        # 最小值
        spec = LLMTaskSpec(
            task_type=TaskType.SUMMARY,
            latency_budget_ms=1000,
        )
        assert spec.latency_budget_ms == 1000

        # 最大值
        spec = LLMTaskSpec(
            task_type=TaskType.SUMMARY,
            latency_budget_ms=120000,
        )
        assert spec.latency_budget_ms == 120000

        # 低于最小值
        with pytest.raises(ValidationError):
            LLMTaskSpec(
                task_type=TaskType.SUMMARY,
                latency_budget_ms=500,
            )

        # 高于最大值
        with pytest.raises(ValidationError):
            LLMTaskSpec(
                task_type=TaskType.SUMMARY,
                latency_budget_ms=200000,
            )

    def test_context_size_non_negative(self):
        """测试 context_size 必须非负"""
        spec = LLMTaskSpec(
            task_type=TaskType.SUMMARY,
            context_size=0,
        )
        assert spec.context_size == 0

        with pytest.raises(ValidationError):
            LLMTaskSpec(
                task_type=TaskType.SUMMARY,
                context_size=-1,
            )

    def test_extra_fields_forbidden(self):
        """测试禁止额外字段"""
        with pytest.raises(ValidationError):
            LLMTaskSpec(
                task_type=TaskType.SUMMARY,
                unknown_field="value",  # type: ignore
            )

    def test_string_task_type_conversion(self):
        """测试字符串 task_type 自动转换"""
        spec = LLMTaskSpec(task_type="fusion_recommendation")  # type: ignore
        assert spec.task_type == TaskType.FUSION_RECOMMENDATION

    def test_model_dump(self):
        """测试模型序列化"""
        spec = LLMTaskSpec(
            task_type=TaskType.FUSION_RECOMMENDATION,
            complexity=Complexity.HIGH,
            need_structured_output=True,
        )

        data = spec.model_dump()

        assert data["task_type"] == "fusion_recommendation"
        assert data["complexity"] == "high"
        assert data["need_structured_output"] is True