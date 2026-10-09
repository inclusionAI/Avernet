"""
Baseline Planner

M4: Research & Planning Engine

最小可用的规划器，基于规则实现。

实现原则：
- 规则可解释
- 优先基于 TaskSpec 字段做规划
- 可以保守，不要求"聪明"
- 不接 LLM
- 所有决策可追溯

规划规则：
1. steps: 从 TaskSpec.subtasks 生成，如果没有则从 goal 生成基础步骤
2. strategy: 基于任务类型和复杂度生成策略摘要
3. role_requirements: 从 TaskSpec.required_capabilities 推断
4. knowledge_requirements: 继承 TaskSpec.required_knowledge
5. resource_requirements: 继承 TaskSpec.required_resources
6. dependencies: 从 subtasks.dependencies 转换
7. risks: 从 high_risk 任务和约束识别
8. assumptions: 从任务上下文推断
9. open_questions: 从 TaskSpec.unknowns 转换
10. fallbacks: 为高风险任务生成备选方案
11. handoff_strategy: 基于任务类型选择
12. escalation_points: 为高风险任务生成
13. confidence: 基于信息完整度计算
"""

from __future__ import annotations

import uuid
from typing import Optional

from src.domain.models.plan_draft import PlanDraft, PlanStep
from src.domain.models.planning_input import PlanningInput
from src.domain.models.planning_result import (
    PlanningResult,
    PlanningWarning,
    PlanningError,
    PlanRisk,
    PlanFallback,
    DependencyRef,
)
from src.domain.models.task_spec import RiskLevel


class BaselinePlanner:
    """
    Baseline 规划器

    基于规则的规划实现，从 TaskSpec 生成 PlanDraft 和规划元数据。
    """

    # 任务类型到角色需求的映射
    TASK_TYPE_ROLES = {
        "设计": ["设计师", "架构师"],
        "架构": ["架构师", "技术负责人"],
        "调研": ["研究员", "分析师"],
        "研究": ["研究员", "分析师"],
        "开发": ["开发者", "工程师"],
        "编码": ["开发者", "工程师"],
        "测试": ["测试工程师", "QA"],
        "文档": ["技术文档工程师"],
        "报告": ["分析师", "文档工程师"],
        "分析": ["分析师", "数据科学家"],
        "组队": ["项目经理", "协调员"],
        "协调": ["协调员", "项目经理"],
    }

    # 高风险关键词（用于风险识别）
    HIGH_RISK_INDICATORS = [
        "生产", "production", "prod",
        "删除", "delete", "remove",
        "外部", "external", "外网",
        "敏感", "sensitive", "机密",
        "权限", "permission", "auth",
        "支付", "payment", "金钱",
        "用户数据", "user data", "隐私",
        "修改", "modify", "update",
        "审批", "approval", "审核",
    ]

    def plan(self, input_data: PlanningInput) -> PlanningResult:
        """
        执行规划

        Args:
            input_data: 规划输入

        Returns:
            PlanningResult: 包含 PlanDraft 和规划元数据的结果
        """
        task_spec = input_data.task_spec

        try:
            # 生成计划 ID
            plan_id = self._generate_plan_id()

            # 生成 objective（从 goal 派生）
            objective = self._derive_objective(task_spec)

            # 生成 steps
            steps = self._generate_steps(task_spec, input_data.planning_hints)

            # 生成 strategy
            strategy = self._generate_strategy(task_spec, steps)

            # 推断 role_requirements
            role_requirements = self._infer_role_requirements(task_spec)

            # 继承 knowledge_requirements 和 resource_requirements
            knowledge_requirements = list(task_spec.required_knowledge)
            resource_requirements = list(task_spec.required_resources)

            # 生成 dependencies
            dependencies = self._generate_dependencies(task_spec, steps)

            # 识别 risks
            risks = self._identify_risks(task_spec)

            # 生成 assumptions
            assumptions = self._generate_assumptions(task_spec, input_data)

            # 转换 unknowns 到 open_questions
            open_questions = list(task_spec.unknowns)

            # 生成 fallbacks
            fallbacks = self._generate_fallbacks(task_spec, risks)

            # 生成 handoff_strategy
            handoff_strategy = self._determine_handoff_strategy(task_spec)

            # 生成 escalation_points
            escalation_points = self._generate_escalation_points(task_spec)

            # 计算 confidence
            confidence = self._calculate_confidence(task_spec, input_data)

            # 确定状态
            status = self._determine_status(task_spec, open_questions, risks)

            # 创建 PlanDraft
            plan_draft = PlanDraft(
                task_id=task_spec.id,
                strategy=strategy,
                steps=steps,
                role_requirements=role_requirements,
                knowledge_requirements=knowledge_requirements,
                resource_requirements=resource_requirements,
                handoff_strategy=handoff_strategy,
                escalation_points=escalation_points,
            )

            # 创建 PlanningResult
            result = PlanningResult(
                plan_draft=plan_draft,
                objective=objective,
                dependencies=dependencies,
                risks=risks,
                assumptions=assumptions,
                open_questions=open_questions,
                fallbacks=fallbacks,
                status=status,
                confidence=confidence,
            )

            # 添加 warnings
            result.warnings.extend(self._generate_warnings(task_spec, input_data, open_questions))

            return result

        except Exception as e:
            # 捕获异常，返回带有 error 的结果
            return self._create_error_result(str(e), input_data)

    def _generate_plan_id(self) -> str:
        """生成计划 ID"""
        return f"plan_{uuid.uuid4().hex[:12]}"

    def _derive_objective(self, task_spec) -> str:
        """从 goal 派生 objective"""
        return task_spec.goal

    def _generate_steps(self, task_spec, planning_hints: dict) -> list[PlanStep]:
        """生成计划步骤"""
        steps = []

        # 规划提示中的最大步骤数
        max_steps = planning_hints.get("max_steps", 10) if planning_hints else 10

        # 优先从 subtasks 生成
        if task_spec.subtasks:
            for i, subtask in enumerate(task_spec.subtasks[:max_steps]):
                step = PlanStep(
                    id=f"step_{i+1}",
                    title=subtask.title,
                    objective=subtask.objective,
                    required_capabilities=[],
                    risk_notes=[],
                )
                steps.append(step)

        # 如果没有 subtasks，从 goal 生成基础步骤
        if not steps:
            # 生成基础的三步计划
            steps.append(PlanStep(
                id="step_1",
                title="分析任务",
                objective=f"分析任务需求：{task_spec.goal[:50]}",
                required_capabilities=["分析能力"],
                risk_notes=[],
            ))

            if len(steps) < max_steps:
                steps.append(PlanStep(
                    id="step_2",
                    title="执行任务",
                    objective="按计划执行任务",
                    required_capabilities=task_spec.required_capabilities[:3],
                    risk_notes=[],
                ))

            if len(steps) < max_steps:
                steps.append(PlanStep(
                    id="step_3",
                    title="验证和交付",
                    objective=f"验证输出并交付：{', '.join(task_spec.deliverables[:2])}",
                    required_capabilities=["验证能力"],
                    risk_notes=[],
                ))

        return steps

    def _generate_strategy(self, task_spec, steps: list[PlanStep]) -> str:
        """生成策略摘要"""
        step_count = len(steps)
        risk_level = task_spec.risk_level
        # 处理 risk_level 可能是字符串或枚举的情况
        risk_value = risk_level.value if hasattr(risk_level, 'value') else risk_level
        risk_desc = "高风险" if risk_value in ["high", "critical"] else "低风险"

        if step_count == 1:
            return f"单步执行策略：{task_spec.goal[:100]}（{risk_desc}）"
        else:
            return f"多步骤执行策略，共 {step_count} 步：{task_spec.goal[:80]}（{risk_desc}）"

    def _infer_role_requirements(self, task_spec) -> list[str]:
        """推断角色需求"""
        roles = set()

        # 从 required_capabilities 推断
        for cap in task_spec.required_capabilities:
            # 简单映射
            if "设计" in cap or "架构" in cap:
                roles.add("架构师")
            if "开发" in cap or "编程" in cap:
                roles.add("开发者")
            if "测试" in cap:
                roles.add("测试工程师")
            if "分析" in cap:
                roles.add("分析师")
            if "协调" in cap or "管理" in cap:
                roles.add("项目经理")
            if "文档" in cap or "写作" in cap:
                roles.add("文档工程师")

        # 如果没有推断出角色，添加默认
        if not roles:
            roles.add("执行者")

        return list(roles)

    def _generate_dependencies(self, task_spec, steps: list[PlanStep]) -> list[DependencyRef]:
        """生成依赖关系"""
        dependencies = []

        # 从 subtasks.dependencies 转换
        if task_spec.subtasks:
            for subtask in task_spec.subtasks:
                for dep_id in subtask.dependencies:
                    # 找到对应的 step
                    dep_step_id = None
                    for i, s in enumerate(task_spec.subtasks):
                        if s.id == dep_id:
                            dep_step_id = f"step_{i+1}"
                            break

                    if dep_step_id:
                        # 找到当前 subtask 对应的 step
                        for i, s in enumerate(task_spec.subtasks):
                            if s.id == subtask.id:
                                dependencies.append(DependencyRef(
                                    from_step=f"step_{i+1}",
                                    to_step=dep_step_id,
                                    dependency_type="sequential",
                                ))
                                break

        # 如果没有从 subtasks 生成的依赖，为多个步骤生成顺序依赖
        if not dependencies and len(steps) > 1:
            for i in range(1, len(steps)):
                dependencies.append(DependencyRef(
                    from_step=f"step_{i+1}",
                    to_step=f"step_{i}",
                    dependency_type="sequential",
                ))

        return dependencies

    def _identify_risks(self, task_spec) -> list[PlanRisk]:
        """识别风险"""
        risks = []

        # 获取 risk_level 的值（处理字符串和枚举两种情况）
        risk_level = task_spec.risk_level
        risk_value = risk_level.value if hasattr(risk_level, 'value') else risk_level

        # 高风险任务
        if risk_value in ["high", "critical"]:
            risks.append(PlanRisk(
                risk_id="risk_high_level",
                description=f"任务风险等级为 {risk_value}，需要特别关注",
                severity="high" if risk_value == "high" else "critical",
                mitigation="建议：增加审批流程和监控",
            ))

        # 从约束识别风险
        for constraint in task_spec.constraints:
            constraint_lower = constraint.lower()
            for indicator in self.HIGH_RISK_INDICATORS:
                if indicator in constraint_lower:
                    risks.append(PlanRisk(
                        risk_id=f"risk_constraint_{len(risks)}",
                        description=f"约束中包含高风险项：{constraint[:50]}",
                        severity="medium",
                        mitigation="建议：制定应对策略",
                    ))
                    break

        # 从 deliverables 识别风险
        if len(task_spec.deliverables) > 5:
            risks.append(PlanRisk(
                risk_id="risk_deliverables",
                description=f"交付物数量较多（{len(task_spec.deliverables)}个），可能影响交付质量",
                severity="low",
                mitigation="建议：分阶段交付",
            ))

        # 从资源需求识别风险
        if task_spec.required_resources and len(task_spec.required_resources) > 3:
            risks.append(PlanRisk(
                risk_id="risk_resources",
                description="依赖较多外部资源，可能存在可用性风险",
                severity="medium",
                mitigation="建议：准备备用资源",
            ))

        return risks

    def _generate_assumptions(self, task_spec, input_data: PlanningInput) -> list[str]:
        """生成假设"""
        assumptions = []

        # 基本假设
        if not task_spec.constraints:
            assumptions.append("无特殊约束限制")

        if not task_spec.required_resources:
            assumptions.append("不需要特殊资源")

        if not task_spec.unknowns:
            assumptions.append("任务信息完整")

        # 基于 source_prompt 的假设
        if input_data.source_prompt:
            assumptions.append("用户原始输入可用")

        # 基于规划提示的假设
        if input_data.planning_hints:
            assumptions.append("规划提示可用")

        return assumptions

    def _generate_fallbacks(self, task_spec, risks: list[PlanRisk]) -> list[PlanFallback]:
        """生成备选方案"""
        fallbacks = []

        # 获取 risk_level 的值
        risk_level = task_spec.risk_level
        risk_value = risk_level.value if hasattr(risk_level, 'value') else risk_level

        # 为高风险任务生成备选
        if risk_value in ["high", "critical"]:
            fallbacks.append(PlanFallback(
                fallback_id="fb_high_risk",
                trigger="主方案执行失败或遇到阻塞",
                action="暂停执行，等待人工干预和审批",
            ))

        # 为资源依赖生成备选
        if len(task_spec.required_resources) > 0:
            fallbacks.append(PlanFallback(
                fallback_id="fb_resources",
                trigger="关键资源不可用",
                action="使用替代资源或调整任务范围",
            ))

        # 为高风险创建备选
        for risk in risks:
            if risk.severity in ["high", "critical"]:
                fallbacks.append(PlanFallback(
                    fallback_id=f"fb_{risk.risk_id}",
                    trigger=f"风险触发：{risk.description[:30]}",
                    action=risk.mitigation or "采取缓解措施",
                ))

        return fallbacks

    def _determine_handoff_strategy(self, task_spec) -> str:
        """确定交接策略"""
        risk_level = task_spec.risk_level
        risk_value = risk_level.value if hasattr(risk_level, 'value') else risk_level

        if risk_value in ["high", "critical"]:
            return "manual_approval"  # 需要人工审批
        elif len(task_spec.subtasks) > 5:
            return "phased"  # 分阶段交接
        else:
            return "auto"  # 自动交接

    def _generate_escalation_points(self, task_spec) -> list[str]:
        """生成升级点"""
        escalation_points = []

        # 获取 risk_level 的值
        risk_level = task_spec.risk_level
        risk_value = risk_level.value if hasattr(risk_level, 'value') else risk_level

        # 高风险任务的升级点
        if risk_value in ["high", "critical"]:
            escalation_points.append("任务开始前需获得审批")
            escalation_points.append("关键步骤执行前需确认")
            escalation_points.append("异常情况立即上报")

        # 复杂任务的升级点
        if len(task_spec.subtasks) > 3:
            escalation_points.append("子任务完成后进行阶段检查")

        # 有未知项的升级点
        if task_spec.unknowns:
            escalation_points.append("未知项需要澄清后再执行")

        return escalation_points

    def _calculate_confidence(self, task_spec, input_data: PlanningInput) -> float:
        """计算置信度"""
        confidence = 1.0

        # unknowns 降低置信度
        confidence -= len(task_spec.unknowns) * 0.1

        # 获取 risk_level 的值
        risk_level = task_spec.risk_level
        risk_value = risk_level.value if hasattr(risk_level, 'value') else risk_level

        # 高风险降低置信度
        if risk_value == "high":
            confidence -= 0.1
        elif risk_value == "critical":
            confidence -= 0.2

        # 缺少能力描述降低置信度
        if not task_spec.required_capabilities:
            confidence -= 0.1

        # 有 source_prompt 提高置信度
        if input_data.source_prompt:
            confidence += 0.05

        # 有 planning_hints 提高置信度
        if input_data.planning_hints:
            confidence += 0.05

        # 限制在有效范围内
        return max(0.0, min(1.0, confidence))

    def _determine_status(self, task_spec, open_questions: list[str], risks: list[PlanRisk]) -> str:
        """确定计划状态"""
        # 有阻塞性风险或关键未知项
        critical_risks = [r for r in risks if r.severity in ["high", "critical"]]
        if critical_risks or len(open_questions) > 3:
            return "blocked"

        # 有待确认问题
        if open_questions:
            return "draft"

        return "ready"

    def _generate_warnings(
        self,
        task_spec,
        input_data: PlanningInput,
        open_questions: list[str]
    ) -> list[PlanningWarning]:
        """生成警告"""
        warnings = []

        # 从 unknowns 生成警告
        if open_questions:
            warnings.append(PlanningWarning(
                field="open_questions",
                message=f"发现 {len(open_questions)} 个待确认问题",
                suggestion="请提供更多信息以获得更准确的计划",
            ))

        # 从 understanding_warnings 转换
        for uw in input_data.understanding_warnings:
            warnings.append(PlanningWarning(
                field=f"understanding.{uw.field}",
                message=uw.message,
                suggestion=uw.suggestion,
            ))

        # 获取 risk_level 的值
        risk_level = task_spec.risk_level
        risk_value = risk_level.value if hasattr(risk_level, 'value') else risk_level

        # 高风险任务警告
        if risk_value in ["high", "critical"]:
            warnings.append(PlanningWarning(
                field="risk_level",
                message="任务风险等级较高，建议增加审批流程",
                suggestion="考虑分阶段执行和人工检查点",
            ))

        return warnings

    def _create_error_result(
        self, error_message: str, input_data: PlanningInput
    ) -> PlanningResult:
        """
        创建错误结果

        Args:
            error_message: 错误消息
            input_data: 原始输入数据

        Returns:
            包含错误的规划结果
        """
        # 创建最小的 PlanDraft
        plan_draft = PlanDraft(
            task_id=input_data.task_spec.id,
            strategy="规划失败",
            steps=[
                PlanStep(
                    id="step_1",
                    title="错误",
                    objective="规划过程中发生错误",
                ),
            ],
            role_requirements=[],
            knowledge_requirements=[],
            resource_requirements=[],
            handoff_strategy="manual",
            escalation_points=[],
        )

        result = PlanningResult(
            plan_draft=plan_draft,
            objective="规划失败",
        )
        result.errors.append(PlanningError(
            field="planning",
            message=f"Planning failed: {error_message}",
            severity="critical",
        ))
        return result


__all__ = ["BaselinePlanner"]