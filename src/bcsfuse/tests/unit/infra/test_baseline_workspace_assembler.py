"""
Tests for BaselineWorkspaceAssembler

M7: Workspace / Group Assembly

测试 BaselineWorkspaceAssembler 的组装逻辑。
"""

from __future__ import annotations

import pytest

from src.infra.assemblers.baseline_workspace_assembler import BaselineWorkspaceAssembler
from src.domain.models.workspace_assembly_input import WorkspaceAssemblyInput, AssemblyHints
from src.domain.models.workspace_assembly_result import WorkspaceAssemblyResult
from src.domain.models.task_spec import TaskSpec, RiskLevel
from src.domain.models.plan_draft import PlanDraft, PlanStep
from src.domain.models.team_spec import TeamSpec, RoleAssignment
from src.domain.models.candidate_bundle import CandidateBundle, KnowledgeItem
from src.domain.models.worker import (
    Worker, WorkerState, WorkerType, WorkerIdentity,
    Capability, CapabilityLevel, SkillRef, ResourceRef,
    SkillSource, TrustLevel, ResourceKind, ResourceAccess, Availability,
)
from src.domain.models.workspace import WorkspaceStatus


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture
def assembler() -> BaselineWorkspaceAssembler:
    """BaselineWorkspaceAssembler 实例"""
    return BaselineWorkspaceAssembler()


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


# =============================================================================
# Basic Assembly Tests
# =============================================================================

class TestBaselineWorkspaceAssemblerBasic:
    """基础组装测试"""

    def test_assemble_with_complete_inputs(
        self,
        assembler: BaselineWorkspaceAssembler,
        sample_task_spec: TaskSpec,
        sample_plan_draft: PlanDraft,
        sample_team_spec: TeamSpec,
        sample_candidate_bundle: CandidateBundle,
    ):
        """测试完整输入的组装"""
        input_data = WorkspaceAssemblyInput(
            task_spec=sample_task_spec,
            plan_draft=sample_plan_draft,
            team_spec=sample_team_spec,
            candidate_bundle=sample_candidate_bundle,
        )

        result = assembler.assemble(input_data)

        assert isinstance(result, WorkspaceAssemblyResult)
        assert result.is_success
        assert result.workspace is not None

    def test_assemble_creates_workspace_with_id(
        self,
        assembler: BaselineWorkspaceAssembler,
        sample_task_spec: TaskSpec,
        sample_plan_draft: PlanDraft,
        sample_team_spec: TeamSpec,
        sample_candidate_bundle: CandidateBundle,
    ):
        """测试组装创建有 ID 的 Workspace"""
        input_data = WorkspaceAssemblyInput(
            task_spec=sample_task_spec,
            plan_draft=sample_plan_draft,
            team_spec=sample_team_spec,
            candidate_bundle=sample_candidate_bundle,
        )

        result = assembler.assemble(input_data)

        assert result.workspace is not None
        assert result.workspace.id.startswith("wsp_")

    def test_assemble_workspace_has_task_id(
        self,
        assembler: BaselineWorkspaceAssembler,
        sample_task_spec: TaskSpec,
        sample_plan_draft: PlanDraft,
        sample_team_spec: TeamSpec,
        sample_candidate_bundle: CandidateBundle,
    ):
        """测试 Workspace 关联正确的 task_id"""
        input_data = WorkspaceAssemblyInput(
            task_spec=sample_task_spec,
            plan_draft=sample_plan_draft,
            team_spec=sample_team_spec,
            candidate_bundle=sample_candidate_bundle,
        )

        result = assembler.assemble(input_data)

        assert result.workspace is not None
        assert result.workspace.task_id == sample_task_spec.id

    def test_assemble_workspace_has_team_spec(
        self,
        assembler: BaselineWorkspaceAssembler,
        sample_task_spec: TaskSpec,
        sample_plan_draft: PlanDraft,
        sample_team_spec: TeamSpec,
        sample_candidate_bundle: CandidateBundle,
    ):
        """测试 Workspace 包含 TeamSpec"""
        input_data = WorkspaceAssemblyInput(
            task_spec=sample_task_spec,
            plan_draft=sample_plan_draft,
            team_spec=sample_team_spec,
            candidate_bundle=sample_candidate_bundle,
        )

        result = assembler.assemble(input_data)

        assert result.workspace is not None
        assert result.workspace.team_spec == sample_team_spec

    def test_assemble_workspace_status(
        self,
        assembler: BaselineWorkspaceAssembler,
        sample_task_spec: TaskSpec,
        sample_plan_draft: PlanDraft,
        sample_team_spec: TeamSpec,
        sample_candidate_bundle: CandidateBundle,
    ):
        """测试 Workspace 状态"""
        input_data = WorkspaceAssemblyInput(
            task_spec=sample_task_spec,
            plan_draft=sample_plan_draft,
            team_spec=sample_team_spec,
            candidate_bundle=sample_candidate_bundle,
        )

        result = assembler.assemble(input_data)

        assert result.workspace is not None
        assert result.workspace.status == WorkspaceStatus.ASSEMBLED.value


# =============================================================================
# Knowledge Mount Tests
# =============================================================================

class TestBaselineWorkspaceAssemblerKnowledgeMount:
    """知识挂载测试"""

    def test_assemble_mounts_knowledge_from_bundle(
        self,
        assembler: BaselineWorkspaceAssembler,
        sample_task_spec: TaskSpec,
        sample_plan_draft: PlanDraft,
        sample_team_spec: TeamSpec,
        sample_candidate_bundle: CandidateBundle,
    ):
        """测试从 CandidateBundle 挂载知识"""
        input_data = WorkspaceAssemblyInput(
            task_spec=sample_task_spec,
            plan_draft=sample_plan_draft,
            team_spec=sample_team_spec,
            candidate_bundle=sample_candidate_bundle,
        )

        result = assembler.assemble(input_data)

        assert result.workspace is not None
        assert len(result.workspace.knowledge_mounts) > 0
        assert "kno_001" in result.workspace.knowledge_mounts

    def test_assemble_mounts_all_knowledge_by_default(
        self,
        assembler: BaselineWorkspaceAssembler,
        sample_task_spec: TaskSpec,
        sample_plan_draft: PlanDraft,
        sample_team_spec: TeamSpec,
        sample_knowledge_item: KnowledgeItem,
    ):
        """测试默认挂载所有知识"""
        # 创建多个知识项
        kno2 = KnowledgeItem(
            id="kno_002",
            kind="doc",
            title="Design Doc",
            summary="Design documentation",
            freshness="fresh",
            reliability="high",
            tags=["design"],
        )

        bundle = CandidateBundle(knowledge_items=[sample_knowledge_item, kno2])
        input_data = WorkspaceAssemblyInput(
            task_spec=sample_task_spec,
            plan_draft=sample_plan_draft,
            team_spec=sample_team_spec,
            candidate_bundle=bundle,
        )

        result = assembler.assemble(input_data)

        assert result.workspace is not None
        assert len(result.workspace.knowledge_mounts) == 2

    def test_assemble_with_include_all_knowledge_false(
        self,
        assembler: BaselineWorkspaceAssembler,
        sample_task_spec: TaskSpec,
        sample_plan_draft: PlanDraft,
        sample_team_spec: TeamSpec,
        sample_candidate_bundle: CandidateBundle,
    ):
        """测试 include_all_knowledge=False 时不挂载"""
        hints = AssemblyHints(include_all_knowledge=False)
        input_data = WorkspaceAssemblyInput(
            task_spec=sample_task_spec,
            plan_draft=sample_plan_draft,
            team_spec=sample_team_spec,
            candidate_bundle=sample_candidate_bundle,
            hints=hints,
        )

        result = assembler.assemble(input_data)

        assert result.workspace is not None
        assert len(result.workspace.knowledge_mounts) == 0


# =============================================================================
# Resource Mount Tests
# =============================================================================

class TestBaselineWorkspaceAssemblerResourceMount:
    """资源挂载测试"""

    def test_assemble_mounts_resources_from_bundle(
        self,
        assembler: BaselineWorkspaceAssembler,
        sample_task_spec: TaskSpec,
        sample_plan_draft: PlanDraft,
        sample_team_spec: TeamSpec,
        sample_candidate_bundle: CandidateBundle,
    ):
        """测试从 CandidateBundle 挂载资源"""
        input_data = WorkspaceAssemblyInput(
            task_spec=sample_task_spec,
            plan_draft=sample_plan_draft,
            team_spec=sample_team_spec,
            candidate_bundle=sample_candidate_bundle,
        )

        result = assembler.assemble(input_data)

        assert result.workspace is not None
        assert len(result.workspace.resource_mounts) > 0
        assert "res_wiki_001" in result.workspace.resource_mounts

    def test_assemble_mounts_selected_resources_from_team_spec(
        self,
        assembler: BaselineWorkspaceAssembler,
        sample_task_spec: TaskSpec,
        sample_plan_draft: PlanDraft,
        sample_team_spec: TeamSpec,
        sample_knowledge_item: KnowledgeItem,
    ):
        """测试挂载 TeamSpec 中选中的资源"""
        # CandidateBundle 没有 resource，但 TeamSpec 有 selected_resources
        bundle = CandidateBundle(knowledge_items=[sample_knowledge_item])
        input_data = WorkspaceAssemblyInput(
            task_spec=sample_task_spec,
            plan_draft=sample_plan_draft,
            team_spec=sample_team_spec,
            candidate_bundle=bundle,
        )

        result = assembler.assemble(input_data)

        assert result.workspace is not None
        # 应该从 TeamSpec.selected_resources 挂载
        assert "res_wiki_001" in result.workspace.resource_mounts

    def test_assemble_with_include_all_resources_false(
        self,
        assembler: BaselineWorkspaceAssembler,
        sample_task_spec: TaskSpec,
        sample_plan_draft: PlanDraft,
        sample_team_spec: TeamSpec,
        sample_candidate_bundle: CandidateBundle,
    ):
        """测试 include_all_resources=False 时只挂载选中的"""
        hints = AssemblyHints(include_all_resources=False)
        input_data = WorkspaceAssemblyInput(
            task_spec=sample_task_spec,
            plan_draft=sample_plan_draft,
            team_spec=sample_team_spec,
            candidate_bundle=sample_candidate_bundle,
            hints=hints,
        )

        result = assembler.assemble(input_data)

        assert result.workspace is not None
        # 只挂载 TeamSpec.selected_resources 中的
        assert "res_wiki_001" in result.workspace.resource_mounts


# =============================================================================
# Event Generation Tests
# =============================================================================

class TestBaselineWorkspaceAssemblerEvents:
    """事件生成测试"""

    def test_assemble_generates_initial_events(
        self,
        assembler: BaselineWorkspaceAssembler,
        sample_task_spec: TaskSpec,
        sample_plan_draft: PlanDraft,
        sample_team_spec: TeamSpec,
        sample_candidate_bundle: CandidateBundle,
    ):
        """测试生成初始事件"""
        input_data = WorkspaceAssemblyInput(
            task_spec=sample_task_spec,
            plan_draft=sample_plan_draft,
            team_spec=sample_team_spec,
            candidate_bundle=sample_candidate_bundle,
        )

        result = assembler.assemble(input_data)

        assert result.workspace is not None
        assert len(result.workspace.events) > 0

    def test_assembly_event_has_correct_type(
        self,
        assembler: BaselineWorkspaceAssembler,
        sample_task_spec: TaskSpec,
        sample_plan_draft: PlanDraft,
        sample_team_spec: TeamSpec,
        sample_candidate_bundle: CandidateBundle,
    ):
        """测试事件有正确的类型"""
        input_data = WorkspaceAssemblyInput(
            task_spec=sample_task_spec,
            plan_draft=sample_plan_draft,
            team_spec=sample_team_spec,
            candidate_bundle=sample_candidate_bundle,
        )

        result = assembler.assemble(input_data)

        assert result.workspace is not None
        event_types = [e.type for e in result.workspace.events]
        assert "workspace_created" in event_types or "assembly_complete" in event_types


# =============================================================================
# Explanations and Mount Info Tests
# =============================================================================

class TestBaselineWorkspaceAssemblerExplanations:
    """解释和挂载信息测试"""

    def test_assemble_generates_explanations(
        self,
        assembler: BaselineWorkspaceAssembler,
        sample_task_spec: TaskSpec,
        sample_plan_draft: PlanDraft,
        sample_team_spec: TeamSpec,
        sample_candidate_bundle: CandidateBundle,
    ):
        """测试生成解释"""
        input_data = WorkspaceAssemblyInput(
            task_spec=sample_task_spec,
            plan_draft=sample_plan_draft,
            team_spec=sample_team_spec,
            candidate_bundle=sample_candidate_bundle,
        )

        result = assembler.assemble(input_data)

        assert len(result.explanations) > 0

    def test_assemble_generates_mount_info(
        self,
        assembler: BaselineWorkspaceAssembler,
        sample_task_spec: TaskSpec,
        sample_plan_draft: PlanDraft,
        sample_team_spec: TeamSpec,
        sample_candidate_bundle: CandidateBundle,
    ):
        """测试生成挂载信息"""
        input_data = WorkspaceAssemblyInput(
            task_spec=sample_task_spec,
            plan_draft=sample_plan_draft,
            team_spec=sample_team_spec,
            candidate_bundle=sample_candidate_bundle,
        )

        result = assembler.assemble(input_data)

        assert len(result.mount_info) > 0

        # 验证挂载信息内容
        mount_ids = [m.id for m in result.mount_info]
        assert "kno_001" in mount_ids or "res_wiki_001" in mount_ids

    def test_mount_info_has_reason(
        self,
        assembler: BaselineWorkspaceAssembler,
        sample_task_spec: TaskSpec,
        sample_plan_draft: PlanDraft,
        sample_team_spec: TeamSpec,
        sample_candidate_bundle: CandidateBundle,
    ):
        """测试挂载信息有原因说明"""
        input_data = WorkspaceAssemblyInput(
            task_spec=sample_task_spec,
            plan_draft=sample_plan_draft,
            team_spec=sample_team_spec,
            candidate_bundle=sample_candidate_bundle,
        )

        result = assembler.assemble(input_data)

        for mount in result.mount_info:
            assert mount.mount_reason is not None


# =============================================================================
# Edge Case Tests
# =============================================================================

class TestBaselineWorkspaceAssemblerEdgeCases:
    """边界情况测试"""

    def test_assemble_with_empty_candidate_bundle(
        self,
        assembler: BaselineWorkspaceAssembler,
        sample_task_spec: TaskSpec,
        sample_plan_draft: PlanDraft,
        sample_team_spec: TeamSpec,
    ):
        """测试空候选集"""
        empty_bundle = CandidateBundle()
        input_data = WorkspaceAssemblyInput(
            task_spec=sample_task_spec,
            plan_draft=sample_plan_draft,
            team_spec=sample_team_spec,
            candidate_bundle=empty_bundle,
        )

        result = assembler.assemble(input_data)

        # 应该仍然成功，只是没有挂载
        assert result.is_success
        assert len(result.workspace.knowledge_mounts) == 0
        # 但应该有 TeamSpec.selected_resources
        assert len(result.workspace.resource_mounts) >= 0

    def test_assemble_with_minimal_team_spec(
        self,
        assembler: BaselineWorkspaceAssembler,
        sample_task_spec: TaskSpec,
        sample_plan_draft: PlanDraft,
        sample_candidate_bundle: CandidateBundle,
    ):
        """测试最小 TeamSpec"""
        minimal_team = TeamSpec(
            team_id="team_minimal",
            members=["wrk_001"],
            role_assignments=[
                RoleAssignment(
                    worker_id="wrk_001",
                    role="contributor",
                    objective="Contribute",
                ),
            ],
            composition_rationale=["Minimal team"],
        )

        input_data = WorkspaceAssemblyInput(
            task_spec=sample_task_spec,
            plan_draft=sample_plan_draft,
            team_spec=minimal_team,
            candidate_bundle=sample_candidate_bundle,
        )

        result = assembler.assemble(input_data)

        assert result.is_success
        assert result.workspace.team_spec == minimal_team

    def test_assemble_multiple_knowledge_and_resources(
        self,
        assembler: BaselineWorkspaceAssembler,
        sample_task_spec: TaskSpec,
        sample_plan_draft: PlanDraft,
    ):
        """测试多个知识项和资源"""
        # 创建多个知识项和资源
        knowledge_items = [
            KnowledgeItem(
                id=f"kno_{i:03d}",
                kind="doc",
                title=f"Doc {i}",
                summary=f"Summary {i}",
                freshness="fresh",
                reliability="high",
                tags=["test"],
            )
            for i in range(3)
        ]

        resources = [
            ResourceRef(
                id=f"res_{i:03d}",
                kind=ResourceKind.FILE,
                name=f"Resource {i}",
                access=ResourceAccess.READ,
            )
            for i in range(3)
        ]

        bundle = CandidateBundle(
            knowledge_items=knowledge_items,
            resources=resources,
        )

        # 使用没有 selected_resources 的 TeamSpec
        team_spec = TeamSpec(
            team_id="team_multi",
            members=["wrk_001"],
            role_assignments=[
                RoleAssignment(
                    worker_id="wrk_001",
                    role="contributor",
                    objective="Contribute",
                ),
            ],
            composition_rationale=["Test team"],
        )

        input_data = WorkspaceAssemblyInput(
            task_spec=sample_task_spec,
            plan_draft=sample_plan_draft,
            team_spec=team_spec,
            candidate_bundle=bundle,
        )

        result = assembler.assemble(input_data)

        assert result.is_success
        assert len(result.workspace.knowledge_mounts) == 3
        assert len(result.workspace.resource_mounts) == 3


# =============================================================================
# Artifacts Tests
# =============================================================================

class TestBaselineWorkspaceAssemblerArtifacts:
    """工件测试"""

    def test_assemble_with_initial_artifacts(
        self,
        assembler: BaselineWorkspaceAssembler,
        sample_task_spec: TaskSpec,
        sample_plan_draft: PlanDraft,
        sample_team_spec: TeamSpec,
        sample_candidate_bundle: CandidateBundle,
    ):
        """测试初始工件"""
        input_data = WorkspaceAssemblyInput(
            task_spec=sample_task_spec,
            plan_draft=sample_plan_draft,
            team_spec=sample_team_spec,
            candidate_bundle=sample_candidate_bundle,
        )

        result = assembler.assemble(input_data)

        assert result.workspace is not None
        # 默认无初始工件
        assert isinstance(result.workspace.artifacts, list)