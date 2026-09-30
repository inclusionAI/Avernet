"""
BaselineExecutionPacketCompiler

M8: Execution Packet Compiler

基准执行包编译器实现。

职责：
- 从 CompilerInput 编译 ExecutionPacket
- 生成 ContextPack（从知识项、记忆注入）
- 生成 ResourcePack（从候选集和工作空间）
- 生成 SkillPack（从候选集技能）
- 生成 Guardrails（从任务约束）
- 生成 OutputContract（从交付物）
- 生成 LaunchPrompt（从任务目标）

不负责：
- 文件落盘（M9 OpenClaw Adapter）
- OpenClaw workspace 生成
- 任务执行
"""

from __future__ import annotations

from typing import Any

from src.domain.models.execution_packet import (
    ExecutionPacket,
    ContextPack,
    ResourcePack,
    SkillPack,
    Guardrails,
    OutputContract,
)
from src.domain.models.compiler_input import CompilerInput, CompilerHints
from src.domain.models.compiler_result import (
    CompilerResult,
    CompilerExplanation,
    CompilerWarning,
)
from src.domain.services.execution_packet_compiler import ExecutionPacketCompiler


class BaselineExecutionPacketCompiler(ExecutionPacketCompiler):
    """
    基准执行包编译器

    实现基础的 ExecutionPacket 编译逻辑。
    """

    def compile(self, input_data: CompilerInput) -> CompilerResult:
        """
        执行包编译

        Args:
            input_data: 编译输入，包含 TaskSpec、PlanDraft、TeamSpec、CandidateBundle、Workspace 和提示

        Returns:
            CompilerResult: 编译结果，包含 ExecutionPacket、警告、错误和解释
        """
        explanations: list[CompilerExplanation] = []
        warnings: list[CompilerWarning] = []

        # 1. 编译 ContextPack
        context_pack = self._compile_context_pack(input_data, explanations, warnings)

        # 2. 编译 ResourcePack
        resource_pack = self._compile_resource_pack(input_data, explanations)

        # 3. 编译 SkillPack
        skill_pack = self._compile_skill_pack(input_data, explanations)

        # 4. 编译 Guardrails
        guardrails = self._compile_guardrails(input_data, explanations)

        # 5. 编译 OutputContract
        output_contract = self._compile_output_contract(input_data, explanations)

        # 6. 生成 LaunchPrompt
        launch_prompt = self._generate_launch_prompt(input_data, explanations)

        # 7. 创建 ExecutionPacket
        packet = ExecutionPacket(
            task_spec=input_data.task_spec,
            plan_draft=input_data.plan_draft,
            team_spec=input_data.team_spec,
            context_pack=context_pack,
            resource_pack=resource_pack,
            skill_pack=skill_pack,
            guardrails=guardrails,
            output_contract=output_contract,
            launch_prompt=launch_prompt,
        )

        # 8. 添加编译完成解释
        explanations.append(
            CompilerExplanation(
                subject="compilation_complete",
                description="Execution packet compiled successfully",
                details={
                    "task_id": input_data.task_spec.id,
                    "team_size": len(input_data.team_spec.members),
                    "knowledge_count": len(context_pack.knowledge_items),
                    "resource_count": len(resource_pack.resources),
                },
            )
        )

        return CompilerResult(
            packet=packet,
            warnings=warnings,
            errors=[],
            explanations=explanations,
        )

    def _compile_context_pack(
        self,
        input_data: CompilerInput,
        explanations: list[CompilerExplanation],
        warnings: list[CompilerWarning],
    ) -> ContextPack:
        """
        编译 ContextPack

        从候选集的知识项和工作空间的知识挂载点收集上下文。
        """
        knowledge_items = []
        seen_ids: set[str] = set()

        # 1. 从 CandidateBundle 收集知识项
        for item in input_data.candidate_bundle.knowledge_items:
            if item.id not in seen_ids:
                knowledge_items.append(item)
                seen_ids.add(item.id)

        # 2. 生成上下文摘要
        summary = self._generate_context_summary(input_data, knowledge_items)

        # 3. 收集引用
        citations = []
        for item in knowledge_items:
            citations.append(f"{item.id}:{item.title}")

        # 4. 记录解释
        if knowledge_items:
            explanations.append(
                CompilerExplanation(
                    subject="context_compilation",
                    description=f"Included {len(knowledge_items)} knowledge items in context",
                    details={
                        "knowledge_count": len(knowledge_items),
                        "sources": [item.id for item in knowledge_items],
                    },
                )
            )
        else:
            warnings.append(
                CompilerWarning(
                    code="EMPTY_CONTEXT",
                    message="No knowledge items available for context",
                    details={},
                )
            )

        return ContextPack(
            summary=summary,
            knowledge_items=knowledge_items,
            memory_injections=[],  # Baseline 不实现 memory 注入
            citations=citations,
        )

    def _compile_resource_pack(
        self,
        input_data: CompilerInput,
        explanations: list[CompilerExplanation],
    ) -> ResourcePack:
        """
        编译 ResourcePack

        从候选集和工作空间收集资源。
        """
        resources = []
        seen_ids: set[str] = set()

        # 1. 从 CandidateBundle 收集资源
        for resource in input_data.candidate_bundle.resources:
            if resource.id not in seen_ids:
                resources.append(resource)
                seen_ids.add(resource.id)

        # 2. 生成挂载说明
        mount_instructions = []
        for resource in resources:
            mount_instructions.append(
                f"Resource {resource.name} ({resource.id}) available as {resource.access.value} access"
            )

        # 3. 记录解释
        if resources:
            explanations.append(
                CompilerExplanation(
                    subject="resource_compilation",
                    description=f"Included {len(resources)} resources",
                    details={
                        "resource_count": len(resources),
                        "resource_ids": [r.id for r in resources],
                    },
                )
            )

        return ResourcePack(
            resources=resources,
            mount_instructions=mount_instructions,
        )

    def _compile_skill_pack(
        self,
        input_data: CompilerInput,
        explanations: list[CompilerExplanation],
    ) -> SkillPack:
        """
        编译 SkillPack

        从候选集和团队成员收集技能。
        """
        skills = []
        allowlist: list[str] = []
        seen_names: set[str] = set()

        # 1. 从 CandidateBundle 收集技能
        for skill in input_data.candidate_bundle.skills:
            if skill.name not in seen_names:
                skills.append(skill)
                allowlist.append(skill.name)
                seen_names.add(skill.name)

        # 2. 从团队成员收集技能
        for worker in input_data.candidate_bundle.workers:
            for skill in worker.skills:
                if skill.name not in seen_names:
                    skills.append(skill)
                    allowlist.append(skill.name)
                    seen_names.add(skill.name)

        # 3. 从 TeamSpec 收集选中的技能
        for skill_name in input_data.team_spec.selected_skills:
            if skill_name not in seen_names:
                allowlist.append(skill_name)
                seen_names.add(skill_name)

        # 4. 确定是否需要沙箱
        sandbox_required = False
        for skill in skills:
            if skill.trust_level and skill.trust_level.value != "trusted":
                sandbox_required = True
                break

        # 5. 记录解释
        if skills or allowlist:
            explanations.append(
                CompilerExplanation(
                    subject="skill_compilation",
                    description=f"Compiled {len(skills)} skills with {len(allowlist)} in allowlist",
                    details={
                        "skill_count": len(skills),
                        "allowlist": allowlist,
                        "sandbox_required": sandbox_required,
                    },
                )
            )

        return SkillPack(
            skills=skills,
            allowlist=allowlist,
            sandbox_required=sandbox_required,
        )

    def _compile_guardrails(
        self,
        input_data: CompilerInput,
        explanations: list[CompilerExplanation],
    ) -> Guardrails:
        """
        编译 Guardrails

        从任务约束生成护栏规则。
        """
        rules: list[str] = []
        approvals: list[str] = []
        blocked_actions: list[str] = []

        # 1. 从任务约束生成规则
        for constraint in input_data.task_spec.constraints:
            rules.append(f"Constraint: {constraint}")

        # 2. 根据风险等级添加额外规则
        risk_level = input_data.task_spec.risk_level
        if risk_level == "high" or risk_level == "critical":
            rules.append("High risk task: Requires extra caution")
            approvals.append("High risk actions require approval")
            blocked_actions.append("Direct production changes without approval")

        # 3. 根据工作空间添加规则
        for resource_mount in input_data.workspace.resource_mounts:
            rules.append(f"Resource access: {resource_mount}")

        # 4. 记录解释
        if rules or approvals or blocked_actions:
            explanations.append(
                CompilerExplanation(
                    subject="guardrails_compilation",
                    description="Generated guardrails from task constraints",
                    details={
                        "rules_count": len(rules),
                        "approvals_count": len(approvals),
                        "blocked_actions_count": len(blocked_actions),
                    },
                )
            )

        return Guardrails(
            rules=rules,
            approvals=approvals,
            blocked_actions=blocked_actions,
        )

    def _compile_output_contract(
        self,
        input_data: CompilerInput,
        explanations: list[CompilerExplanation],
    ) -> OutputContract:
        """
        编译 OutputContract

        从任务交付物生成输出契约。
        """
        # 1. 交付物作为必需工件
        required_artifacts = list(input_data.task_spec.deliverables)

        # 2. 必需章节
        required_sections = ["Summary", "Decisions"]
        for criterion in input_data.task_spec.success_criteria:
            required_sections.append(f"Validation: {criterion}")

        # 3. 是否必须包含验证
        must_include_validation = len(input_data.task_spec.success_criteria) > 0

        # 4. 格式提示
        format_hints = []
        if input_data.task_spec.constraints:
            format_hints.append("Follow all specified constraints")

        # 5. 记录解释
        explanations.append(
            CompilerExplanation(
                subject="output_contract_compilation",
                description="Generated output contract from task deliverables",
                details={
                    "required_artifacts": required_artifacts,
                    "must_include_validation": must_include_validation,
                },
            )
        )

        return OutputContract(
            required_artifacts=required_artifacts,
            required_sections=required_sections,
            must_include_validation=must_include_validation,
            format_hints=format_hints,
        )

    def _generate_launch_prompt(
        self,
        input_data: CompilerInput,
        explanations: list[CompilerExplanation],
    ) -> str:
        """
        生成 LaunchPrompt

        从任务目标和规划策略生成启动提示。
        """
        parts: list[str] = []

        # 1. 任务目标
        parts.append(f"Goal: {input_data.task_spec.goal}")

        # 2. 策略摘要
        parts.append(f"Strategy: {input_data.plan_draft.strategy}")

        # 3. 团队角色
        parts.append("Team:")
        for assignment in input_data.team_spec.role_assignments:
            parts.append(f"  - {assignment.role}: {assignment.objective}")

        # 4. 交付物
        if input_data.task_spec.deliverables:
            parts.append("Deliverables:")
            for deliverable in input_data.task_spec.deliverables:
                parts.append(f"  - {deliverable}")

        # 5. 记录解释
        explanations.append(
            CompilerExplanation(
                subject="launch_prompt_generation",
                description="Generated launch prompt from task and plan",
                details={
                    "goal": input_data.task_spec.goal,
                    "strategy": input_data.plan_draft.strategy,
                },
            )
        )

        return "\n".join(parts)

    def _generate_context_summary(
        self,
        input_data: CompilerInput,
        knowledge_items: list[Any],
    ) -> str:
        """
        生成上下文摘要
        """
        parts: list[str] = []

        # 1. 任务背景
        parts.append(f"Task: {input_data.task_spec.goal}")

        # 2. 知识项摘要
        if knowledge_items:
            parts.append(f"Knowledge: {len(knowledge_items)} items available")
            tags: set[str] = set()
            for item in knowledge_items:
                tags.update(item.tags)
            if tags:
                parts.append(f"Topics: {', '.join(sorted(tags))}")

        # 3. 需要的知识领域
        if input_data.plan_draft.knowledge_requirements:
            parts.append(f"Required knowledge: {', '.join(input_data.plan_draft.knowledge_requirements)}")

        return ". ".join(parts)


__all__ = ["BaselineExecutionPacketCompiler"]