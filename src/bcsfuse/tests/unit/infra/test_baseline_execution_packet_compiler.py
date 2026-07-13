"""
Tests for BaselineExecutionPacketCompiler

M8: Execution Packet Compiler

测试 BaselineExecutionPacketCompiler 的编译逻辑。
"""

from __future__ import annotations

import pytest

from src.infra.compilers.baseline_execution_packet_compiler import BaselineExecutionPacketCompiler
from src.domain.models.compiler_input import CompilerInput, CompilerHints
from src.domain.models.compiler_result import CompilerResult
from src.domain.models.task_spec import TaskSpec, RiskLevel
from src.domain.models.plan_draft import PlanDraft, PlanStep
from src.domain.models.team_spec import TeamSpec, RoleAssignment
from src.domain.models.candidate_bundle import CandidateBundle, KnowledgeItem
from src.domain.models.workspace import Workspace, WorkspaceStatus
from src.domain.models.worker import (
    Worker, WorkerState, WorkerType, WorkerIdentity,
    Capability, CapabilityLevel, SkillRef, ResourceRef,
    SkillSource, TrustLevel, ResourceKind, ResourceAccess, Availability,
)


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture
def compiler() -> BaselineExecutionPacketCompiler:
    """BaselineExecutionPacketCompiler 实例"""
    return BaselineExecutionPacketCompiler()


@pytest.fixture
def architect_worker() -> Worker:
    """架构师 Worker"""
    return Worker(
        id="wrk_architect_001",
        type=WorkerType.BOT,
        identity=WorkerIdentity(
            name="Architect Bot",
            handle="architect_bot",
            title="System Architect",
        ),
        responsibilities=["Design systems", "Create documentation"],
        capabilities=[
            Capability(name="system_design", level=CapabilityLevel.EXPERT),
            Capability(name="documentation", level=CapabilityLevel.ADVANCED),
        ],
        domains=["architecture"],
        skills=[
            SkillRef(
                name="diagram_generation",
                source=SkillSource.BUILTIN,
                trust_level=TrustLevel.TRUSTED,
            ),
        ],
        state=WorkerState(
            availability=Availability.AVAILABLE,
            trust_level=TrustLevel.TRUSTED,
            current_load=0.0,
        ),
    )


@pytest.fixture
def sample_task_spec() -> TaskSpec:
    """示例 TaskSpec"""
    return TaskSpec(
        id="tsk_architecture_001",
        goal="Design system architecture",
        deliverables=["Architecture document"],
        constraints=["Must use microservices"],
        success_criteria=["Approved by review"],
        required_capabilities=["system_design"],
        required_knowledge=["architecture"],
        required_resources=["res_wiki_001"],
        risk_level=RiskLevel.LOW,
        unknowns=[],
        subtasks=[],
    )


@pytest.fixture
def sample_plan_draft() -> PlanDraft:
    """示例 PlanDraft"""
    return PlanDraft(
        task_id="tsk_architecture_001",
        strategy="Design first",
        steps=[
            PlanStep(id="s1", title="Design", objective="Create design"),
        ],
        role_requirements=["architect"],
        knowledge_requirements=["architecture"],
        resource_requirements=["res_wiki_001"],
        handoff_strategy="sequential",
        escalation_points=[],
    )


@pytest.fixture
def sample_team_spec() -> TeamSpec:
    """示例 TeamSpec"""
    return TeamSpec(
        team_id="team_architecture_001",
        members=["wrk_architect_001"],
        role_assignments=[
            RoleAssignment(
                worker_id="wrk_architect_001",
                role="architect",
                objective="Design architecture",
            ),
        ],
        selected_skills=["diagram_generation"],
        selected_resources=["res_wiki_001"],
        composition_rationale=["Best match for architecture task"],
        gaps=[],
    )


@pytest.fixture
def sample_knowledge_item() -> KnowledgeItem:
    """示例 KnowledgeItem"""
    return KnowledgeItem(
        id="kno_001",
        kind="guide",
        title="Architecture Guide",
        summary="System architecture best practices",
        freshness="fresh",
        reliability="high",
        tags=["architecture", "design"],
    )


@pytest.fixture
def sample_resource_ref() -> ResourceRef:
    """示例 ResourceRef"""
    return ResourceRef(
        id="res_wiki_001",
        kind=ResourceKind.FILE,
        name="Architecture Wiki",
        access=ResourceAccess.READ,
    )


@pytest.fixture
def sample_candidate_bundle(
    architect_worker: Worker,
    sample_knowledge_item: KnowledgeItem,
    sample_resource_ref: ResourceRef,
) -> CandidateBundle:
    """示例 CandidateBundle"""
    return CandidateBundle(
        workers=[architect_worker],
        knowledge_items=[sample_knowledge_item],
        resources=[sample_resource_ref],
    )


@pytest.fixture
def sample_workspace(sample_team_spec: TeamSpec) -> Workspace:
    """示例 Workspace"""
    return Workspace(
        id="wsp_001",
        task_id="tsk_architecture_001",
        team_spec=sample_team_spec,
        knowledge_mounts=["kno_001"],
        resource_mounts=["res_wiki_001"],
        status=WorkspaceStatus.ASSEMBLED,
    )


# =============================================================================
# Basic Compilation Tests
# =============================================================================

class TestBaselineExecutionPacketCompilerBasic:
    """基础编译测试"""

    def test_compile_with_complete_inputs(
        self,
        compiler: BaselineExecutionPacketCompiler,
        sample_task_spec: TaskSpec,
        sample_plan_draft: PlanDraft,
        sample_team_spec: TeamSpec,
        sample_candidate_bundle: CandidateBundle,
        sample_workspace: Workspace,
    ):
        """测试完整输入的编译"""
        input_data = CompilerInput(
            task_spec=sample_task_spec,
            plan_draft=sample_plan_draft,
            team_spec=sample_team_spec,
            candidate_bundle=sample_candidate_bundle,
            workspace=sample_workspace,
        )

        result = compiler.compile(input_data)

        assert isinstance(result, CompilerResult)
        assert result.is_success
        assert result.packet is not None

    def test_compile_creates_packet_with_task_spec(
        self,
        compiler: BaselineExecutionPacketCompiler,
        sample_task_spec: TaskSpec,
        sample_plan_draft: PlanDraft,
        sample_team_spec: TeamSpec,
        sample_candidate_bundle: CandidateBundle,
        sample_workspace: Workspace,
    ):
        """测试编译创建包含 TaskSpec 的 packet"""
        input_data = CompilerInput(
            task_spec=sample_task_spec,
            plan_draft=sample_plan_draft,
            team_spec=sample_team_spec,
            candidate_bundle=sample_candidate_bundle,
            workspace=sample_workspace,
        )

        result = compiler.compile(input_data)

        assert result.packet is not None
        assert result.packet.task_spec == sample_task_spec

    def test_compile_creates_packet_with_plan_draft(
        self,
        compiler: BaselineExecutionPacketCompiler,
        sample_task_spec: TaskSpec,
        sample_plan_draft: PlanDraft,
        sample_team_spec: TeamSpec,
        sample_candidate_bundle: CandidateBundle,
        sample_workspace: Workspace,
    ):
        """测试编译创建包含 PlanDraft 的 packet"""
        input_data = CompilerInput(
            task_spec=sample_task_spec,
            plan_draft=sample_plan_draft,
            team_spec=sample_team_spec,
            candidate_bundle=sample_candidate_bundle,
            workspace=sample_workspace,
        )

        result = compiler.compile(input_data)

        assert result.packet is not None
        assert result.packet.plan_draft == sample_plan_draft

    def test_compile_creates_packet_with_team_spec(
        self,
        compiler: BaselineExecutionPacketCompiler,
        sample_task_spec: TaskSpec,
        sample_plan_draft: PlanDraft,
        sample_team_spec: TeamSpec,
        sample_candidate_bundle: CandidateBundle,
        sample_workspace: Workspace,
    ):
        """测试编译创建包含 TeamSpec 的 packet"""
        input_data = CompilerInput(
            task_spec=sample_task_spec,
            plan_draft=sample_plan_draft,
            team_spec=sample_team_spec,
            candidate_bundle=sample_candidate_bundle,
            workspace=sample_workspace,
        )

        result = compiler.compile(input_data)

        assert result.packet is not None
        assert result.packet.team_spec == sample_team_spec


# =============================================================================
# ContextPack Generation Tests
# =============================================================================

class TestBaselineExecutionPacketCompilerContextPack:
    """ContextPack 生成测试"""

    def test_compile_generates_context_pack(
        self,
        compiler: BaselineExecutionPacketCompiler,
        sample_task_spec: TaskSpec,
        sample_plan_draft: PlanDraft,
        sample_team_spec: TeamSpec,
        sample_candidate_bundle: CandidateBundle,
        sample_workspace: Workspace,
    ):
        """测试生成 ContextPack"""
        input_data = CompilerInput(
            task_spec=sample_task_spec,
            plan_draft=sample_plan_draft,
            team_spec=sample_team_spec,
            candidate_bundle=sample_candidate_bundle,
            workspace=sample_workspace,
        )

        result = compiler.compile(input_data)

        assert result.packet is not None
        assert result.packet.context_pack is not None

    def test_compile_includes_knowledge_items(
        self,
        compiler: BaselineExecutionPacketCompiler,
        sample_task_spec: TaskSpec,
        sample_plan_draft: PlanDraft,
        sample_team_spec: TeamSpec,
        sample_candidate_bundle: CandidateBundle,
        sample_workspace: Workspace,
    ):
        """测试包含知识项"""
        input_data = CompilerInput(
            task_spec=sample_task_spec,
            plan_draft=sample_plan_draft,
            team_spec=sample_team_spec,
            candidate_bundle=sample_candidate_bundle,
            workspace=sample_workspace,
        )

        result = compiler.compile(input_data)

        assert result.packet is not None
        # Should include knowledge from candidate bundle
        assert len(result.packet.context_pack.knowledge_items) > 0

    def test_compile_generates_context_summary(
        self,
        compiler: BaselineExecutionPacketCompiler,
        sample_task_spec: TaskSpec,
        sample_plan_draft: PlanDraft,
        sample_team_spec: TeamSpec,
        sample_candidate_bundle: CandidateBundle,
        sample_workspace: Workspace,
    ):
        """测试生成上下文摘要"""
        input_data = CompilerInput(
            task_spec=sample_task_spec,
            plan_draft=sample_plan_draft,
            team_spec=sample_team_spec,
            candidate_bundle=sample_candidate_bundle,
            workspace=sample_workspace,
        )

        result = compiler.compile(input_data)

        assert result.packet is not None
        assert len(result.packet.context_pack.summary) > 0


# =============================================================================
# ResourcePack Generation Tests
# =============================================================================

class TestBaselineExecutionPacketCompilerResourcePack:
    """ResourcePack 生成测试"""

    def test_compile_generates_resource_pack(
        self,
        compiler: BaselineExecutionPacketCompiler,
        sample_task_spec: TaskSpec,
        sample_plan_draft: PlanDraft,
        sample_team_spec: TeamSpec,
        sample_candidate_bundle: CandidateBundle,
        sample_workspace: Workspace,
    ):
        """测试生成 ResourcePack"""
        input_data = CompilerInput(
            task_spec=sample_task_spec,
            plan_draft=sample_plan_draft,
            team_spec=sample_team_spec,
            candidate_bundle=sample_candidate_bundle,
            workspace=sample_workspace,
        )

        result = compiler.compile(input_data)

        assert result.packet is not None
        assert result.packet.resource_pack is not None

    def test_compile_includes_resources(
        self,
        compiler: BaselineExecutionPacketCompiler,
        sample_task_spec: TaskSpec,
        sample_plan_draft: PlanDraft,
        sample_team_spec: TeamSpec,
        sample_candidate_bundle: CandidateBundle,
        sample_workspace: Workspace,
    ):
        """测试包含资源"""
        input_data = CompilerInput(
            task_spec=sample_task_spec,
            plan_draft=sample_plan_draft,
            team_spec=sample_team_spec,
            candidate_bundle=sample_candidate_bundle,
            workspace=sample_workspace,
        )

        result = compiler.compile(input_data)

        assert result.packet is not None
        assert len(result.packet.resource_pack.resources) > 0


# =============================================================================
# SkillPack Generation Tests
# =============================================================================

class TestBaselineExecutionPacketCompilerSkillPack:
    """SkillPack 生成测试"""

    def test_compile_generates_skill_pack(
        self,
        compiler: BaselineExecutionPacketCompiler,
        sample_task_spec: TaskSpec,
        sample_plan_draft: PlanDraft,
        sample_team_spec: TeamSpec,
        sample_candidate_bundle: CandidateBundle,
        sample_workspace: Workspace,
    ):
        """测试生成 SkillPack"""
        input_data = CompilerInput(
            task_spec=sample_task_spec,
            plan_draft=sample_plan_draft,
            team_spec=sample_team_spec,
            candidate_bundle=sample_candidate_bundle,
            workspace=sample_workspace,
        )

        result = compiler.compile(input_data)

        assert result.packet is not None
        assert result.packet.skill_pack is not None

    def test_compile_sets_sandbox_required(
        self,
        compiler: BaselineExecutionPacketCompiler,
        sample_task_spec: TaskSpec,
        sample_plan_draft: PlanDraft,
        sample_team_spec: TeamSpec,
        sample_candidate_bundle: CandidateBundle,
        sample_workspace: Workspace,
    ):
        """测试设置沙箱要求"""
        input_data = CompilerInput(
            task_spec=sample_task_spec,
            plan_draft=sample_plan_draft,
            team_spec=sample_team_spec,
            candidate_bundle=sample_candidate_bundle,
            workspace=sample_workspace,
        )

        result = compiler.compile(input_data)

        assert result.packet is not None
        # sandbox_required should be set (True or False)
        assert isinstance(result.packet.skill_pack.sandbox_required, bool)


# =============================================================================
# Guardrails Generation Tests
# =============================================================================

class TestBaselineExecutionPacketCompilerGuardrails:
    """Guardrails 生成测试"""

    def test_compile_generates_guardrails(
        self,
        compiler: BaselineExecutionPacketCompiler,
        sample_task_spec: TaskSpec,
        sample_plan_draft: PlanDraft,
        sample_team_spec: TeamSpec,
        sample_candidate_bundle: CandidateBundle,
        sample_workspace: Workspace,
    ):
        """测试生成 Guardrails"""
        input_data = CompilerInput(
            task_spec=sample_task_spec,
            plan_draft=sample_plan_draft,
            team_spec=sample_team_spec,
            candidate_bundle=sample_candidate_bundle,
            workspace=sample_workspace,
        )

        result = compiler.compile(input_data)

        assert result.packet is not None
        assert result.packet.guardrails is not None

    def test_compile_includes_constraints_as_rules(
        self,
        compiler: BaselineExecutionPacketCompiler,
        sample_task_spec: TaskSpec,
        sample_plan_draft: PlanDraft,
        sample_team_spec: TeamSpec,
        sample_candidate_bundle: CandidateBundle,
        sample_workspace: Workspace,
    ):
        """测试包含约束作为规则"""
        input_data = CompilerInput(
            task_spec=sample_task_spec,
            plan_draft=sample_plan_draft,
            team_spec=sample_team_spec,
            candidate_bundle=sample_candidate_bundle,
            workspace=sample_workspace,
        )

        result = compiler.compile(input_data)

        assert result.packet is not None
        # Should have guardrails rules
        assert result.packet.guardrails.rules is not None


# =============================================================================
# OutputContract Generation Tests
# =============================================================================

class TestBaselineExecutionPacketCompilerOutputContract:
    """OutputContract 生成测试"""

    def test_compile_generates_output_contract(
        self,
        compiler: BaselineExecutionPacketCompiler,
        sample_task_spec: TaskSpec,
        sample_plan_draft: PlanDraft,
        sample_team_spec: TeamSpec,
        sample_candidate_bundle: CandidateBundle,
        sample_workspace: Workspace,
    ):
        """测试生成 OutputContract"""
        input_data = CompilerInput(
            task_spec=sample_task_spec,
            plan_draft=sample_plan_draft,
            team_spec=sample_team_spec,
            candidate_bundle=sample_candidate_bundle,
            workspace=sample_workspace,
        )

        result = compiler.compile(input_data)

        assert result.packet is not None
        assert result.packet.output_contract is not None

    def test_compile_includes_deliverables(
        self,
        compiler: BaselineExecutionPacketCompiler,
        sample_task_spec: TaskSpec,
        sample_plan_draft: PlanDraft,
        sample_team_spec: TeamSpec,
        sample_candidate_bundle: CandidateBundle,
        sample_workspace: Workspace,
    ):
        """测试包含交付物"""
        input_data = CompilerInput(
            task_spec=sample_task_spec,
            plan_draft=sample_plan_draft,
            team_spec=sample_team_spec,
            candidate_bundle=sample_candidate_bundle,
            workspace=sample_workspace,
        )

        result = compiler.compile(input_data)

        assert result.packet is not None
        # Should have required artifacts from deliverables
        assert len(result.packet.output_contract.required_artifacts) > 0


# =============================================================================
# LaunchPrompt Generation Tests
# =============================================================================

class TestBaselineExecutionPacketCompilerLaunchPrompt:
    """LaunchPrompt 生成测试"""

    def test_compile_generates_launch_prompt(
        self,
        compiler: BaselineExecutionPacketCompiler,
        sample_task_spec: TaskSpec,
        sample_plan_draft: PlanDraft,
        sample_team_spec: TeamSpec,
        sample_candidate_bundle: CandidateBundle,
        sample_workspace: Workspace,
    ):
        """测试生成 LaunchPrompt"""
        input_data = CompilerInput(
            task_spec=sample_task_spec,
            plan_draft=sample_plan_draft,
            team_spec=sample_team_spec,
            candidate_bundle=sample_candidate_bundle,
            workspace=sample_workspace,
        )

        result = compiler.compile(input_data)

        assert result.packet is not None
        assert len(result.packet.launch_prompt) > 0


# =============================================================================
# Explanations Tests
# =============================================================================

class TestBaselineExecutionPacketCompilerExplanations:
    """解释生成测试"""

    def test_compile_generates_explanations(
        self,
        compiler: BaselineExecutionPacketCompiler,
        sample_task_spec: TaskSpec,
        sample_plan_draft: PlanDraft,
        sample_team_spec: TeamSpec,
        sample_candidate_bundle: CandidateBundle,
        sample_workspace: Workspace,
    ):
        """测试生成解释"""
        input_data = CompilerInput(
            task_spec=sample_task_spec,
            plan_draft=sample_plan_draft,
            team_spec=sample_team_spec,
            candidate_bundle=sample_candidate_bundle,
            workspace=sample_workspace,
        )

        result = compiler.compile(input_data)

        assert len(result.explanations) > 0

    def test_explanations_have_subjects(
        self,
        compiler: BaselineExecutionPacketCompiler,
        sample_task_spec: TaskSpec,
        sample_plan_draft: PlanDraft,
        sample_team_spec: TeamSpec,
        sample_candidate_bundle: CandidateBundle,
        sample_workspace: Workspace,
    ):
        """测试解释有主题"""
        input_data = CompilerInput(
            task_spec=sample_task_spec,
            plan_draft=sample_plan_draft,
            team_spec=sample_team_spec,
            candidate_bundle=sample_candidate_bundle,
            workspace=sample_workspace,
        )

        result = compiler.compile(input_data)

        for exp in result.explanations:
            assert exp.subject is not None
            assert len(exp.subject) > 0


# =============================================================================
# Edge Case Tests
# =============================================================================

class TestBaselineExecutionPacketCompilerEdgeCases:
    """边界情况测试"""

    def test_compile_with_empty_candidate_bundle(
        self,
        compiler: BaselineExecutionPacketCompiler,
        sample_task_spec: TaskSpec,
        sample_plan_draft: PlanDraft,
        sample_team_spec: TeamSpec,
        sample_workspace: Workspace,
    ):
        """测试空候选集"""
        empty_bundle = CandidateBundle()
        input_data = CompilerInput(
            task_spec=sample_task_spec,
            plan_draft=sample_plan_draft,
            team_spec=sample_team_spec,
            candidate_bundle=empty_bundle,
            workspace=sample_workspace,
        )

        result = compiler.compile(input_data)

        # Should still succeed
        assert result.is_success
        assert result.packet is not None
        # But knowledge_items should be empty
        assert len(result.packet.context_pack.knowledge_items) == 0

    def test_compile_with_minimal_workspace(
        self,
        compiler: BaselineExecutionPacketCompiler,
        sample_task_spec: TaskSpec,
        sample_plan_draft: PlanDraft,
        sample_team_spec: TeamSpec,
        sample_candidate_bundle: CandidateBundle,
    ):
        """测试最小 Workspace"""
        minimal_workspace = Workspace(
            id="wsp_minimal",
            task_id="tsk_architecture_001",
            team_spec=sample_team_spec,
            status=WorkspaceStatus.DRAFT,
        )
        input_data = CompilerInput(
            task_spec=sample_task_spec,
            plan_draft=sample_plan_draft,
            team_spec=sample_team_spec,
            candidate_bundle=sample_candidate_bundle,
            workspace=minimal_workspace,
        )

        result = compiler.compile(input_data)

        assert result.is_success


# =============================================================================
# Platform Independence Tests
# =============================================================================

class TestBaselineExecutionPacketCompilerPlatformIndependence:
    """平台无关性测试"""

    def test_packet_does_not_contain_openclaw_fields(
        self,
        compiler: BaselineExecutionPacketCompiler,
        sample_task_spec: TaskSpec,
        sample_plan_draft: PlanDraft,
        sample_team_spec: TeamSpec,
        sample_candidate_bundle: CandidateBundle,
        sample_workspace: Workspace,
    ):
        """测试 packet 不包含 OpenClaw 特定字段"""
        input_data = CompilerInput(
            task_spec=sample_task_spec,
            plan_draft=sample_plan_draft,
            team_spec=sample_team_spec,
            candidate_bundle=sample_candidate_bundle,
            workspace=sample_workspace,
        )

        result = compiler.compile(input_data)

        assert result.packet is not None
        # Verify no OpenClaw-specific fields
        assert not hasattr(result.packet, "task_md_path")
        assert not hasattr(result.packet, "team_md_path")
        assert not hasattr(result.packet, "context_md_path")
        assert not hasattr(result.packet, "manifest")
        assert not hasattr(result.packet, "file_digests")

    def test_launch_prompt_does_not_contain_openclaw_instructions(
        self,
        compiler: BaselineExecutionPacketCompiler,
        sample_task_spec: TaskSpec,
        sample_plan_draft: PlanDraft,
        sample_team_spec: TeamSpec,
        sample_candidate_bundle: CandidateBundle,
        sample_workspace: Workspace,
    ):
        """测试 launch_prompt 不包含 OpenClaw 加载指令"""
        input_data = CompilerInput(
            task_spec=sample_task_spec,
            plan_draft=sample_plan_draft,
            team_spec=sample_team_spec,
            candidate_bundle=sample_candidate_bundle,
            workspace=sample_workspace,
        )

        result = compiler.compile(input_data)

        assert result.packet is not None
        # Verify no OpenClaw-specific instructions
        prompt = result.packet.launch_prompt.lower()
        assert "openclaw" not in prompt
        assert "agents.md" not in prompt
        assert "workspace/" not in prompt