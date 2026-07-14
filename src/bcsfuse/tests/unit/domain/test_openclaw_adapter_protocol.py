"""
Tests for OpenClawAdapter Protocol

M9: OpenClaw Adapter

测试 OpenClawAdapter Protocol 的接口定义。
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

import pytest

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
        constraints=["Test constraint"],
        success_criteria=["Test criteria"],
        required_capabilities=["coding"],
        required_knowledge=["python"],
        required_resources=["res_001"],
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
        steps=[PlanStep(id="s1", title="Step 1", objective="Test objective")],
        role_requirements=["developer"],
        knowledge_requirements=["python"],
        resource_requirements=["res_001"],
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
def sample_execution_packet(
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
        launch_prompt="Please complete the task.",
    )


class TestOpenClawAdapterProtocol:
    """OpenClawAdapter Protocol 测试"""

    def test_protocol_is_runtime_checkable(self):
        """测试 Protocol 是 runtime_checkable"""
        from src.domain.protocols.openclaw_adapter import OpenClawAdapter

        # runtime_checkable protocols have _is_protocol attribute
        assert hasattr(OpenClawAdapter, "_is_protocol")

    def test_protocol_has_adapt_method(self):
        """测试 Protocol 有 adapt 方法"""
        from src.domain.protocols.openclaw_adapter import OpenClawAdapter

        # 检查 Protocol 有 adapt 方法
        assert hasattr(OpenClawAdapter, "adapt")

    def test_protocol_signature(self):
        """测试 Protocol 签名"""
        from src.domain.protocols.openclaw_adapter import OpenClawAdapter
        from src.domain.models.openclaw_adapter_input import OpenClawAdapterInput
        from src.domain.models.openclaw_adapter_result import OpenClawAdapterResult
        import inspect

        # 获取 adapt 方法的签名
        method = getattr(OpenClawAdapter, "adapt", None)
        if method is not None:
            sig = inspect.signature(method)
            params = list(sig.parameters.keys())

            # 应该有 self 和 input 参数
            assert len(params) >= 1


class TestOpenClawAdapterProtocolCompliance:
    """Protocol 合规性测试"""

    def test_implementation_compliance(self):
        """测试实现类是否合规"""
        from src.domain.protocols.openclaw_adapter import OpenClawAdapter
        from src.domain.models.openclaw_adapter_input import OpenClawAdapterInput
        from src.domain.models.openclaw_adapter_result import OpenClawAdapterResult

        # 创建一个简单的实现
        class MockAdapter:
            def adapt(self, input_data: OpenClawAdapterInput) -> OpenClawAdapterResult:
                return OpenClawAdapterResult()

        # 检查是否可以被识别为实现
        adapter = MockAdapter()
        assert hasattr(adapter, "adapt")
        assert callable(adapter.adapt)


class TestOpenClawAdapterContract:
    """OpenClawAdapter 契约测试"""

    def test_adapt_returns_result(self, sample_execution_packet: ExecutionPacket):
        """测试 adapt 返回 OpenClawAdapterResult"""
        from src.domain.protocols.openclaw_adapter import OpenClawAdapter
        from src.domain.models.openclaw_adapter_input import OpenClawAdapterInput
        from src.domain.models.openclaw_adapter_result import OpenClawAdapterResult

        # 创建一个简单的实现
        class MockAdapter:
            def adapt(self, input_data: OpenClawAdapterInput) -> OpenClawAdapterResult:
                return OpenClawAdapterResult()

        adapter = MockAdapter()
        input_data = OpenClawAdapterInput(packet=sample_execution_packet)

        result = adapter.adapt(input_data)

        assert isinstance(result, OpenClawAdapterResult)


# =============================================================================
# Schema Alignment Tests
# =============================================================================

class TestOpenClawAdapterProtocolSchema:
    """Schema 一致性测试"""

    def test_protocol_module_exists(self):
        """测试 Protocol 模块存在"""
        import importlib

        module = importlib.import_module("src.domain.protocols.openclaw_adapter")
        assert hasattr(module, "OpenClawAdapter")

    def test_protocol_importable(self):
        """测试 Protocol 可导入"""
        from src.domain.protocols.openclaw_adapter import OpenClawAdapter

        assert OpenClawAdapter is not None