"""
Tests for Planning Input Model

M4: Research & Planning Engine

测试范围：
- PlanningInput 构造与字段校验
- 必需字段验证
- 可选字段验证
- 与 TaskSpec 的关联
"""

from __future__ import annotations

import pytest


class TestPlanningInputBasics:
    """测试 PlanningInput 基础构造"""

    def test_planning_input_importable(self):
        """测试 PlanningInput 可导入"""
        from src.domain.models.planning_input import PlanningInput

        assert PlanningInput is not None

    def test_planning_input_constructs_with_required_fields(self):
        """测试使用必需字段构造 PlanningInput"""
        from src.domain.models.planning_input import PlanningInput
        from tests.fixtures.planning_inputs import get_simple_design_task_spec

        task_spec = get_simple_design_task_spec()
        planning_input = PlanningInput(task_spec=task_spec)

        assert planning_input.task_spec is not None
        assert planning_input.task_spec.id == "tsk_design_001"

    def test_planning_input_accepts_optional_understanding_warnings(self):
        """测试 PlanningInput 接受可选的 understanding_warnings"""
        from src.domain.models.planning_input import PlanningInput
        from tests.fixtures.planning_inputs import (
            get_simple_design_task_spec,
            get_understanding_warnings,
        )

        task_spec = get_simple_design_task_spec()
        warnings = get_understanding_warnings()

        planning_input = PlanningInput(
            task_spec=task_spec,
            understanding_warnings=warnings,
        )

        assert len(planning_input.understanding_warnings) == 1
        assert planning_input.understanding_warnings[0].field == "unknowns"

    def test_planning_input_accepts_optional_understanding_errors(self):
        """测试 PlanningInput 接受可选的 understanding_errors"""
        from src.domain.models.planning_input import PlanningInput
        from src.domain.models.task_understanding_result import UnderstandingError
        from tests.fixtures.planning_inputs import get_simple_design_task_spec

        task_spec = get_simple_design_task_spec()
        errors = [
            UnderstandingError(
                field="goal",
                message="Goal is too vague",
                severity="medium",
            )
        ]

        planning_input = PlanningInput(
            task_spec=task_spec,
            understanding_errors=errors,
        )

        assert len(planning_input.understanding_errors) == 1

    def test_planning_input_accepts_optional_source_prompt(self):
        """测试 PlanningInput 接受可选的 source_prompt"""
        from src.domain.models.planning_input import PlanningInput
        from tests.fixtures.planning_inputs import get_simple_design_task_spec

        task_spec = get_simple_design_task_spec()
        planning_input = PlanningInput(
            task_spec=task_spec,
            source_prompt="帮我设计一个系统架构方案",
        )

        assert planning_input.source_prompt == "帮我设计一个系统架构方案"

    def test_planning_input_accepts_optional_planning_hints(self):
        """测试 PlanningInput 接受可选的 planning_hints"""
        from src.domain.models.planning_input import PlanningInput
        from tests.fixtures.planning_inputs import get_simple_design_task_spec

        task_spec = get_simple_design_task_spec()
        planning_input = PlanningInput(
            task_spec=task_spec,
            planning_hints={"prefer_small_team": True, "max_steps": 5},
        )

        assert planning_input.planning_hints["prefer_small_team"] is True
        assert planning_input.planning_hints["max_steps"] == 5


class TestPlanningInputValidation:
    """测试 PlanningInput 验证"""

    def test_planning_input_requires_task_spec(self):
        """测试 PlanningInput 必需 task_spec"""
        from src.domain.models.planning_input import PlanningInput
        from pydantic import ValidationError

        with pytest.raises(ValidationError) as exc_info:
            PlanningInput()

        errors = exc_info.value.errors()
        assert any(e["loc"][0] == "task_spec" for e in errors)

    def test_planning_input_rejects_extra_fields(self):
        """测试 PlanningInput 拒绝额外字段"""
        from src.domain.models.planning_input import PlanningInput
        from tests.fixtures.planning_inputs import get_simple_design_task_spec
        from pydantic import ValidationError

        task_spec = get_simple_design_task_spec()

        with pytest.raises(ValidationError):
            PlanningInput(
                task_spec=task_spec,
                extra_field="not_allowed",
            )


class TestPlanningInputDefaults:
    """测试 PlanningInput 默认值"""

    def test_understanding_warnings_defaults_to_empty_list(self):
        """测试 understanding_warnings 默认为空列表"""
        from src.domain.models.planning_input import PlanningInput
        from tests.fixtures.planning_inputs import get_simple_design_task_spec

        task_spec = get_simple_design_task_spec()
        planning_input = PlanningInput(task_spec=task_spec)

        assert planning_input.understanding_warnings == []

    def test_understanding_errors_defaults_to_empty_list(self):
        """测试 understanding_errors 默认为空列表"""
        from src.domain.models.planning_input import PlanningInput
        from tests.fixtures.planning_inputs import get_simple_design_task_spec

        task_spec = get_simple_design_task_spec()
        planning_input = PlanningInput(task_spec=task_spec)

        assert planning_input.understanding_errors == []

    def test_source_prompt_defaults_to_none(self):
        """测试 source_prompt 默认为 None"""
        from src.domain.models.planning_input import PlanningInput
        from tests.fixtures.planning_inputs import get_simple_design_task_spec

        task_spec = get_simple_design_task_spec()
        planning_input = PlanningInput(task_spec=task_spec)

        assert planning_input.source_prompt is None

    def test_planning_hints_defaults_to_empty_dict(self):
        """测试 planning_hints 默认为空字典"""
        from src.domain.models.planning_input import PlanningInput
        from tests.fixtures.planning_inputs import get_simple_design_task_spec

        task_spec = get_simple_design_task_spec()
        planning_input = PlanningInput(task_spec=task_spec)

        assert planning_input.planning_hints == {}