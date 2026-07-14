"""
Tests for OpenClawAdapterInput Domain Model

M9: OpenClaw Adapter

测试 OpenClawAdapterInput 和 AdapterOptions 模型的构造、字段校验和行为。
"""

from __future__ import annotations

import pytest

from src.domain.models.openclaw_adapter_input import (
    OpenClawAdapterInput,
    AdapterOptions,
)
from src.domain.models.execution_packet import (
    ExecutionPacket,
    ContextPack,
    ResourcePack,
    SkillPack,
    Guardrails,
    OutputContract,
)
from src.domain.models.task_spec import TaskSpec, RiskLevel
from src.domain.models.plan_draft import PlanDraft, PlanStep
from src.domain.models.team_spec import TeamSpec, RoleAssignment


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture
def sample_task_spec() -> TaskSpec:
    """示例 TaskSpec"""
    return TaskSpec(
        id="tsk_test_001",
        goal="Test goal",
        deliverables=["Test deliverable"],
        constraints=[],
        success_criteria=["Success"],
        required_capabilities=["coding"],
        required_knowledge=[],
        required_resources=[],
        risk_level=RiskLevel.LOW,
        unknowns=[],
        subtasks=[],
    )


@pytest.fixture
def sample_plan_draft() -> PlanDraft:
    """示例 PlanDraft"""
    return PlanDraft(
        task_id="tsk_test_001",
        strategy="Test strategy",
        steps=[PlanStep(id="s1", title="Step 1", objective="Test")],
        role_requirements=["developer"],
        knowledge_requirements=[],
        resource_requirements=[],
        handoff_strategy="sequential",
        escalation_points=[],
    )


@pytest.fixture
def sample_team_spec() -> TeamSpec:
    """示例 TeamSpec"""
    return TeamSpec(
        team_id="team_test_001",
        members=["wrk_001"],
        role_assignments=[
            RoleAssignment(
                worker_id="wrk_001",
                role="developer",
                objective="Develop",
            ),
        ],
        composition_rationale=["Test team"],
    )


@pytest.fixture
def sample_packet(
    sample_task_spec: TaskSpec,
    sample_plan_draft: PlanDraft,
    sample_team_spec: TeamSpec,
) -> ExecutionPacket:
    """示例 ExecutionPacket"""
    return ExecutionPacket(
        task_spec=sample_task_spec,
        plan_draft=sample_plan_draft,
        team_spec=sample_team_spec,
        context_pack=ContextPack(summary="Test context"),
        resource_pack=ResourcePack(),
        skill_pack=SkillPack(sandbox_required=False),
        guardrails=Guardrails(),
        output_contract=OutputContract(must_include_validation=True),
        launch_prompt="Test prompt",
    )


# =============================================================================
# AdapterOptions Tests
# =============================================================================

class TestAdapterOptions:
    """AdapterOptions 测试"""

    def test_create_default_options(self):
        """测试创建默认 AdapterOptions"""
        options = AdapterOptions()

        assert options.include_memory_files is True
        assert options.strict_skill_whitelist is True
        assert options.generate_manifest is True

    def test_create_options_with_values(self):
        """测试创建带值的 AdapterOptions"""
        options = AdapterOptions(
            include_memory_files=False,
            strict_skill_whitelist=False,
            generate_manifest=False,
        )

        assert options.include_memory_files is False
        assert options.strict_skill_whitelist is False
        assert options.generate_manifest is False

    def test_options_extra_fields_forbidden(self):
        """测试禁止额外字段"""
        with pytest.raises(Exception):  # ValidationError
            AdapterOptions(
                extra_field="invalid",  # type: ignore
            )


# =============================================================================
# OpenClawAdapterInput Tests
# =============================================================================

class TestOpenClawAdapterInput:
    """OpenClawAdapterInput 测试"""

    def test_create_input_with_packet(self, sample_packet: ExecutionPacket):
        """测试创建带 packet 的输入"""
        input_data = OpenClawAdapterInput(packet=sample_packet)

        assert input_data.packet == sample_packet
        assert input_data.options is not None

    def test_input_default_options(self, sample_packet: ExecutionPacket):
        """测试默认 options"""
        input_data = OpenClawAdapterInput(packet=sample_packet)

        assert input_data.options.include_memory_files is True
        assert input_data.options.strict_skill_whitelist is True

    def test_input_with_custom_options(self, sample_packet: ExecutionPacket):
        """测试自定义 options"""
        options = AdapterOptions(
            include_memory_files=False,
            strict_skill_whitelist=True,
        )
        input_data = OpenClawAdapterInput(
            packet=sample_packet,
            options=options,
        )

        assert input_data.options.include_memory_files is False
        assert input_data.options.strict_skill_whitelist is True

    def test_input_required_fields(self):
        """测试必需字段验证"""
        with pytest.raises(Exception):  # ValidationError
            OpenClawAdapterInput()  # Missing packet

    def test_input_extra_fields_forbidden(self, sample_packet: ExecutionPacket):
        """测试禁止额外字段"""
        with pytest.raises(Exception):  # ValidationError
            OpenClawAdapterInput(
                packet=sample_packet,
                extra_field="invalid",  # type: ignore
            )


# =============================================================================
# Schema Alignment Tests
# =============================================================================

class TestOpenClawAdapterInputSchemaAlignment:
    """Schema 一致性测试"""

    def test_input_has_required_fields(self, sample_packet: ExecutionPacket):
        """测试输入有所有必需字段"""
        input_data = OpenClawAdapterInput(packet=sample_packet)

        assert hasattr(input_data, "packet")
        assert hasattr(input_data, "options")

    def test_options_has_all_fields(self):
        """测试 options 有所有字段"""
        options = AdapterOptions()

        assert hasattr(options, "include_memory_files")
        assert hasattr(options, "strict_skill_whitelist")
        assert hasattr(options, "generate_manifest")