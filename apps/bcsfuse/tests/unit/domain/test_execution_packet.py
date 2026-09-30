"""
Tests for ExecutionPacket Domain Model

M8: Execution Packet Compiler

测试 ExecutionPacket 及其辅助模型的构造、字段校验和行为。
"""

from __future__ import annotations

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
from src.domain.models.candidate_bundle import KnowledgeItem
from src.domain.models.worker import SkillRef, ResourceRef, SkillSource, ResourceKind, ResourceAccess, TrustLevel


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
def sample_knowledge_item() -> KnowledgeItem:
    """示例 KnowledgeItem"""
    return KnowledgeItem(
        id="kno_001",
        kind="doc",
        title="Test Knowledge",
        summary="Test summary",
        freshness="fresh",
        reliability="high",
        tags=["test"],
    )


@pytest.fixture
def sample_skill_ref() -> SkillRef:
    """示例 SkillRef"""
    return SkillRef(
        name="test_skill",
        source=SkillSource.BUILTIN,
        trust_level=TrustLevel.TRUSTED,
    )


@pytest.fixture
def sample_resource_ref() -> ResourceRef:
    """示例 ResourceRef"""
    return ResourceRef(
        id="res_001",
        kind=ResourceKind.FILE,
        name="Test Resource",
        access=ResourceAccess.READ,
    )


# =============================================================================
# ContextPack Tests
# =============================================================================

class TestContextPack:
    """ContextPack 测试"""

    def test_create_context_pack_with_required_fields(self):
        """测试创建 ContextPack"""
        context = ContextPack(
            summary="Test context summary",
        )

        assert context.summary == "Test context summary"
        assert context.knowledge_items == []
        assert context.memory_injections == []
        assert context.citations == []

    def test_context_pack_with_knowledge_items(
        self,
        sample_knowledge_item: KnowledgeItem,
    ):
        """测试带知识项的 ContextPack"""
        context = ContextPack(
            summary="Context with knowledge",
            knowledge_items=[sample_knowledge_item],
        )

        assert len(context.knowledge_items) == 1
        assert context.knowledge_items[0].id == "kno_001"

    def test_context_pack_with_memory_injections(self):
        """测试带记忆注入的 ContextPack"""
        context = ContextPack(
            summary="Context with memory",
            memory_injections=["Previous context about X", "Historical decision Y"],
        )

        assert len(context.memory_injections) == 2

    def test_context_pack_with_citations(self):
        """测试带引用的 ContextPack"""
        context = ContextPack(
            summary="Context with citations",
            citations=["doc_001", "doc_002"],
        )

        assert len(context.citations) == 2

    def test_context_pack_extra_fields_forbidden(self):
        """测试禁止额外字段"""
        with pytest.raises(Exception):  # ValidationError
            ContextPack(
                summary="Test",
                extra_field="invalid",  # type: ignore
            )


# =============================================================================
# ResourcePack Tests
# =============================================================================

class TestResourcePack:
    """ResourcePack 测试"""

    def test_create_resource_pack_with_required_fields(self):
        """测试创建 ResourcePack"""
        pack = ResourcePack()

        assert pack.resources == []
        assert pack.mount_instructions == []

    def test_resource_pack_with_resources(
        self,
        sample_resource_ref: ResourceRef,
    ):
        """测试带资源的 ResourcePack"""
        pack = ResourcePack(
            resources=[sample_resource_ref],
            mount_instructions=["Mount at /data/resources"],
        )

        assert len(pack.resources) == 1
        assert len(pack.mount_instructions) == 1

    def test_resource_pack_extra_fields_forbidden(self):
        """测试禁止额外字段"""
        with pytest.raises(Exception):  # ValidationError
            ResourcePack(
                extra_field="invalid",  # type: ignore
            )


# =============================================================================
# SkillPack Tests
# =============================================================================

class TestSkillPack:
    """SkillPack 测试"""

    def test_create_skill_pack_with_required_fields(self):
        """测试创建 SkillPack"""
        pack = SkillPack(sandbox_required=False)

        assert pack.skills == []
        assert pack.allowlist == []
        assert pack.sandbox_required is False

    def test_skill_pack_with_skills(
        self,
        sample_skill_ref: SkillRef,
    ):
        """测试带技能的 SkillPack"""
        pack = SkillPack(
            skills=[sample_skill_ref],
            allowlist=["test_skill"],
            sandbox_required=True,
        )

        assert len(pack.skills) == 1
        assert len(pack.allowlist) == 1
        assert pack.sandbox_required is True

    def test_skill_pack_extra_fields_forbidden(self):
        """测试禁止额外字段"""
        with pytest.raises(Exception):  # ValidationError
            SkillPack(
                sandbox_required=False,
                extra_field="invalid",  # type: ignore
            )


# =============================================================================
# Guardrails Tests
# =============================================================================

class TestGuardrails:
    """Guardrails 测试"""

    def test_create_guardrails_with_required_fields(self):
        """测试创建 Guardrails"""
        guardrails = Guardrails()

        assert guardrails.rules == []
        assert guardrails.approvals == []
        assert guardrails.blocked_actions == []

    def test_guardrails_with_rules(self):
        """测试带规则的 Guardrails"""
        guardrails = Guardrails(
            rules=["No production writes", "No external calls"],
            approvals=["Require approval for database changes"],
            blocked_actions=["delete_users", "send_external_email"],
        )

        assert len(guardrails.rules) == 2
        assert len(guardrails.approvals) == 1
        assert len(guardrails.blocked_actions) == 2

    def test_guardrails_extra_fields_forbidden(self):
        """测试禁止额外字段"""
        with pytest.raises(Exception):  # ValidationError
            Guardrails(
                extra_field="invalid",  # type: ignore
            )


# =============================================================================
# OutputContract Tests
# =============================================================================

class TestOutputContract:
    """OutputContract 测试"""

    def test_create_output_contract_with_required_fields(self):
        """测试创建 OutputContract"""
        contract = OutputContract(must_include_validation=True)

        assert contract.required_artifacts == []
        assert contract.required_sections == []
        assert contract.must_include_validation is True
        assert contract.format_hints == []

    def test_output_contract_with_all_fields(self):
        """测试带所有字段的 OutputContract"""
        contract = OutputContract(
            required_artifacts=["design.md", "review.md"],
            required_sections=["Summary", "Decisions"],
            must_include_validation=True,
            format_hints=["Use markdown", "Include diagrams"],
        )

        assert len(contract.required_artifacts) == 2
        assert len(contract.required_sections) == 2
        assert len(contract.format_hints) == 2

    def test_output_contract_extra_fields_forbidden(self):
        """测试禁止额外字段"""
        with pytest.raises(Exception):  # ValidationError
            OutputContract(
                must_include_validation=True,
                extra_field="invalid",  # type: ignore
            )


# =============================================================================
# ExecutionPacket Tests
# =============================================================================

class TestExecutionPacket:
    """ExecutionPacket 测试"""

    def test_create_execution_packet_with_required_fields(
        self,
        sample_task_spec: TaskSpec,
        sample_plan_draft: PlanDraft,
        sample_team_spec: TeamSpec,
    ):
        """测试创建 ExecutionPacket"""
        packet = ExecutionPacket(
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

        assert packet.task_spec == sample_task_spec
        assert packet.plan_draft == sample_plan_draft
        assert packet.team_spec == sample_team_spec
        assert packet.context_pack.summary == "Test context"
        assert packet.launch_prompt == "Please complete the task."

    def test_execution_packet_launch_prompt_min_length(
        self,
        sample_task_spec: TaskSpec,
        sample_plan_draft: PlanDraft,
        sample_team_spec: TeamSpec,
    ):
        """测试 launch_prompt 最小长度"""
        with pytest.raises(Exception):  # ValidationError
            ExecutionPacket(
                task_spec=sample_task_spec,
                plan_draft=sample_plan_draft,
                team_spec=sample_team_spec,
                context_pack=ContextPack(summary="Test"),
                resource_pack=ResourcePack(),
                skill_pack=SkillPack(sandbox_required=False),
                guardrails=Guardrails(),
                output_contract=OutputContract(must_include_validation=True),
                launch_prompt="",  # Empty should fail
            )

    def test_execution_packet_extra_fields_forbidden(
        self,
        sample_task_spec: TaskSpec,
        sample_plan_draft: PlanDraft,
        sample_team_spec: TeamSpec,
    ):
        """测试禁止额外字段"""
        with pytest.raises(Exception):  # ValidationError
            ExecutionPacket(
                task_spec=sample_task_spec,
                plan_draft=sample_plan_draft,
                team_spec=sample_team_spec,
                context_pack=ContextPack(summary="Test"),
                resource_pack=ResourcePack(),
                skill_pack=SkillPack(sandbox_required=False),
                guardrails=Guardrails(),
                output_contract=OutputContract(must_include_validation=True),
                launch_prompt="Test",
                extra_field="invalid",  # type: ignore
            )


# =============================================================================
# Schema Alignment Tests
# =============================================================================

class TestExecutionPacketSchemaAlignment:
    """Schema 一致性测试"""

    def test_execution_packet_has_required_fields(
        self,
        sample_task_spec: TaskSpec,
        sample_plan_draft: PlanDraft,
        sample_team_spec: TeamSpec,
    ):
        """测试 ExecutionPacket 有所有必需字段"""
        packet = ExecutionPacket(
            task_spec=sample_task_spec,
            plan_draft=sample_plan_draft,
            team_spec=sample_team_spec,
            context_pack=ContextPack(summary="Test"),
            resource_pack=ResourcePack(),
            skill_pack=SkillPack(sandbox_required=False),
            guardrails=Guardrails(),
            output_contract=OutputContract(must_include_validation=True),
            launch_prompt="Test",
        )

        # 验证所有 schema 定义的必需字段
        assert hasattr(packet, "task_spec")
        assert hasattr(packet, "plan_draft")
        assert hasattr(packet, "team_spec")
        assert hasattr(packet, "context_pack")
        assert hasattr(packet, "resource_pack")
        assert hasattr(packet, "skill_pack")
        assert hasattr(packet, "guardrails")
        assert hasattr(packet, "output_contract")
        assert hasattr(packet, "launch_prompt")

    def test_context_pack_has_required_fields(self):
        """测试 ContextPack 有所有必需字段"""
        context = ContextPack(summary="Test")

        assert hasattr(context, "summary")
        assert hasattr(context, "knowledge_items")
        assert hasattr(context, "memory_injections")
        assert hasattr(context, "citations")

    def test_resource_pack_has_required_fields(self):
        """测试 ResourcePack 有所有必需字段"""
        pack = ResourcePack()

        assert hasattr(pack, "resources")
        assert hasattr(pack, "mount_instructions")

    def test_skill_pack_has_required_fields(self):
        """测试 SkillPack 有所有必需字段"""
        pack = SkillPack(sandbox_required=False)

        assert hasattr(pack, "skills")
        assert hasattr(pack, "allowlist")
        assert hasattr(pack, "sandbox_required")

    def test_guardrails_has_required_fields(self):
        """测试 Guardrails 有所有必需字段"""
        guardrails = Guardrails()

        assert hasattr(guardrails, "rules")
        assert hasattr(guardrails, "approvals")
        assert hasattr(guardrails, "blocked_actions")

    def test_output_contract_has_required_fields(self):
        """测试 OutputContract 有所有必需字段"""
        contract = OutputContract(must_include_validation=True)

        assert hasattr(contract, "required_artifacts")
        assert hasattr(contract, "required_sections")
        assert hasattr(contract, "must_include_validation")


# =============================================================================
# Platform Independence Tests
# =============================================================================

class TestExecutionPacketPlatformIndependence:
    """平台无关性测试"""

    def test_packet_does_not_contain_openclaw_file_paths(
        self,
        sample_task_spec: TaskSpec,
        sample_plan_draft: PlanDraft,
        sample_team_spec: TeamSpec,
    ):
        """测试 ExecutionPacket 不包含 OpenClaw 文件路径"""
        packet = ExecutionPacket(
            task_spec=sample_task_spec,
            plan_draft=sample_plan_draft,
            team_spec=sample_team_spec,
            context_pack=ContextPack(summary="Test"),
            resource_pack=ResourcePack(),
            skill_pack=SkillPack(sandbox_required=False),
            guardrails=Guardrails(),
            output_contract=OutputContract(must_include_validation=True),
            launch_prompt="Test",
        )

        # 验证不存在 OpenClaw 特定字段
        assert not hasattr(packet, "task_md_path")
        assert not hasattr(packet, "team_md_path")
        assert not hasattr(packet, "context_md_path")
        assert not hasattr(packet, "agents_md_path")
        assert not hasattr(packet, "soul_md_path")
        assert not hasattr(packet, "tools_md_path")

    def test_packet_does_not_contain_openclaw_manifest(
        self,
        sample_task_spec: TaskSpec,
        sample_plan_draft: PlanDraft,
        sample_team_spec: TeamSpec,
    ):
        """测试 ExecutionPacket 不包含 OpenClaw manifest"""
        packet = ExecutionPacket(
            task_spec=sample_task_spec,
            plan_draft=sample_plan_draft,
            team_spec=sample_team_spec,
            context_pack=ContextPack(summary="Test"),
            resource_pack=ResourcePack(),
            skill_pack=SkillPack(sandbox_required=False),
            guardrails=Guardrails(),
            output_contract=OutputContract(must_include_validation=True),
            launch_prompt="Test",
        )

        # 验证不存在 OpenClaw 落盘相关字段
        assert not hasattr(packet, "manifest")
        assert not hasattr(packet, "file_digests")
        assert not hasattr(packet, "workspace_path")


# =============================================================================
# Edge Case Tests
# =============================================================================

class TestExecutionPacketEdgeCases:
    """边界情况测试"""

    def test_full_packet_with_all_components(
        self,
        sample_task_spec: TaskSpec,
        sample_plan_draft: PlanDraft,
        sample_team_spec: TeamSpec,
        sample_knowledge_item: KnowledgeItem,
        sample_skill_ref: SkillRef,
        sample_resource_ref: ResourceRef,
    ):
        """测试完整的 ExecutionPacket"""
        packet = ExecutionPacket(
            task_spec=sample_task_spec,
            plan_draft=sample_plan_draft,
            team_spec=sample_team_spec,
            context_pack=ContextPack(
                summary="Full context",
                knowledge_items=[sample_knowledge_item],
                memory_injections=["Memory 1"],
                citations=["cite_001"],
            ),
            resource_pack=ResourcePack(
                resources=[sample_resource_ref],
                mount_instructions=["Mount instruction"],
            ),
            skill_pack=SkillPack(
                skills=[sample_skill_ref],
                allowlist=["test_skill"],
                sandbox_required=True,
            ),
            guardrails=Guardrails(
                rules=["Rule 1"],
                approvals=["Approval 1"],
                blocked_actions=["blocked_1"],
            ),
            output_contract=OutputContract(
                required_artifacts=["artifact_1"],
                required_sections=["section_1"],
                must_include_validation=True,
                format_hints=["hint_1"],
            ),
            launch_prompt="Complete this task following all guidelines.",
        )

        assert packet.task_spec.id == "tsk_test_001"
        assert len(packet.context_pack.knowledge_items) == 1
        assert len(packet.resource_pack.resources) == 1
        assert len(packet.skill_pack.skills) == 1
        assert len(packet.guardrails.rules) == 1
        assert len(packet.output_contract.required_artifacts) == 1