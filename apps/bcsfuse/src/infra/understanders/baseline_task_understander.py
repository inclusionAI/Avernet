"""
Baseline Task Understander

M3: Task Understanding Engine

最小可用的任务理解器，基于规则实现。

实现原则：
- 规则可解释
- 优先基于模式、关键词、句式、显式约束提取
- 可以保守，不要求"聪明"
- 不接 LLM
- 所有决策可追溯

抽取规则：
1. Goal: 从请求中提取主要动词+宾语作为目标
2. Deliverables: 从"输出"、"生成"、"交付"等关键词推断交付物
3. Constraints: 从"必须"、"不能"、"要求"等关键词抽取约束
4. Success Criteria: 从"要求"、"标准"、"通过"等关键词推断
5. Required Capabilities: 从任务类型推断所需能力
6. Risk Level: 从"生产"、"外部"、"删除"等高风险关键词判定
7. Unknowns: 对缺失信息进行标记
8. Subtasks: 从"然后"、"接着"、"第一步"等步骤词拆解
"""

from __future__ import annotations

import re
import uuid
from typing import Optional

from src.domain.models.task_spec import TaskSpec, RiskLevel, Subtask
from src.domain.models.task_understanding_input import TaskUnderstandingInput
from src.domain.models.task_understanding_result import (
    TaskUnderstandingResult,
    UnderstandingWarning,
    UnderstandingError,
)


class BaselineTaskUnderstander:
    """
    Baseline 任务理解器

    基于规则的任务理解实现，支持基础的任务归一化。
    """

    # 高风险关键词
    HIGH_RISK_KEYWORDS = [
        "生产", "production", "prod",
        "删除", "delete", "remove",
        "外部", "external", "外网",
        "敏感", "sensitive", "机密",
        "权限", "permission", "auth",
        "支付", "payment", "金钱",
        "用户数据", "user data", "隐私",
        "修改", "modify", "update",
        "直接", "immediately",
        "邮件", "email", "mail",
    ]

    # 任务类型到能力的映射
    TASK_TYPE_CAPABILITIES = {
        "设计": ["系统设计", "架构设计"],
        "架构": ["系统设计", "架构设计"],
        "调研": ["信息检索", "分析能力"],
        "研究": ["信息检索", "分析能力"],
        "开发": ["软件开发", "编程能力"],
        "编码": ["软件开发", "编程能力"],
        "测试": ["测试能力", "质量保证"],
        "文档": ["文档编写", "技术写作"],
        "报告": ["文档编写", "分析能力"],
        "分析": ["分析能力", "数据处理"],
        "组队": ["团队协调", "项目管理"],
        "协调": ["团队协调", "沟通能力"],
    }

    # 步骤分隔符
    STEP_SEPARATORS = ["然后", "接着", "之后", "再", "第一步", "第二步", "第三步", "1.", "2.", "3."]

    # 交付物关键词
    DELIVERABLE_KEYWORDS = ["输出", "生成", "交付", "产出", "结果", "报告", "文档", "方案", "代码"]

    # 约束关键词
    CONSTRAINT_KEYWORDS = ["必须", "不能", "禁止", "要求", "限制", "只能", "不可", "不要"]

    # 成功标准关键词
    SUCCESS_KEYWORDS = ["要求", "标准", "通过", "完成", "成功", "满足"]

    def understand(self, input_data: TaskUnderstandingInput) -> TaskUnderstandingResult:
        """
        理解任务并生成 TaskSpec

        Args:
            input_data: 任务理解输入

        Returns:
            TaskUnderstandingResult: 包含 TaskSpec、warnings 和 errors
        """
        result = TaskUnderstandingResult(source_prompt=input_data.raw_request)

        try:
            # 生成任务 ID
            task_id = self._generate_task_id()

            # 抽取 goal
            goal = self._extract_goal(input_data)

            # 抽取 deliverables
            deliverables = self._extract_deliverables(input_data)

            # 抽取 constraints
            constraints = self._extract_constraints(input_data)

            # 抽取 success_criteria
            success_criteria = self._extract_success_criteria(input_data, deliverables)

            # 推断 required_capabilities
            required_capabilities = self._infer_capabilities(input_data)

            # 推断 required_knowledge
            required_knowledge = self._infer_knowledge(input_data)

            # 推断 required_resources
            required_resources = self._infer_resources(input_data)

            # 判定 risk_level
            risk_level = self._determine_risk_level(input_data)

            # 识别 unknowns
            unknowns = self._identify_unknowns(input_data)

            # 拆解 subtasks
            subtasks = self._decompose_subtasks(input_data)

            # 创建 TaskSpec
            task_spec = TaskSpec(
                id=task_id,
                goal=goal,
                deliverables=deliverables,
                constraints=constraints,
                success_criteria=success_criteria,
                required_capabilities=required_capabilities,
                required_knowledge=required_knowledge,
                required_resources=required_resources,
                risk_level=risk_level,
                unknowns=unknowns,
                subtasks=subtasks,
                source_prompt=input_data.raw_request,
                metadata=input_data.metadata,
            )

            result.task_spec = task_spec

            # 添加 warnings
            if len(unknowns) > 0:
                result.warnings.append(UnderstandingWarning(
                    field="unknowns",
                    message=f"发现 {len(unknowns)} 个未知项需要澄清",
                    suggestion="请提供更多细节以获得更准确的任务规格",
                ))

        except Exception as e:
            result.errors.append(UnderstandingError(
                field="understanding",
                message=f"任务理解失败: {str(e)}",
                severity="high",
            ))

        return result

    def _generate_task_id(self) -> str:
        """生成任务 ID"""
        return f"tsk_{uuid.uuid4().hex[:12]}"

    def _extract_goal(self, input_data: TaskUnderstandingInput) -> str:
        """抽取目标"""
        raw = input_data.raw_request

        # 简单清洗
        raw = raw.strip()

        # 提取主要动词和宾语
        # 这里使用简单的规则，不做复杂 NLP
        goal = raw

        # 尝试提取核心意图
        # 常见模式: "帮我X", "请X", "需要X"
        patterns = [
            r"帮我(.+)",
            r"请帮我(.+)",
            r"需要(.+)",
            r"想要(.+)",
            r"我要(.+)",
        ]

        for pattern in patterns:
            match = re.search(pattern, raw)
            if match:
                goal = match.group(1).strip()
                break

        # 限制长度
        if len(goal) > 200:
            goal = goal[:200] + "..."

        return goal

    def _extract_deliverables(self, input_data: TaskUnderstandingInput) -> list[str]:
        """抽取交付物"""
        deliverables = []
        raw = input_data.raw_request
        context = input_data.context or ""

        combined = raw + " " + context

        # 从关键词推断交付物
        if "报告" in combined or "调研" in combined:
            deliverables.append("调研报告")
        if "方案" in combined or "设计" in combined:
            deliverables.append("设计方案")
        if "文档" in combined:
            deliverables.append("技术文档")
        if "代码" in combined or "开发" in combined:
            deliverables.append("代码")
        if "PPT" in combined or "ppt" in combined.lower():
            deliverables.append("演示文稿")
        if "测试" in combined:
            deliverables.append("测试报告")

        # 默认交付物
        if not deliverables:
            deliverables.append("任务产出")

        return deliverables

    def _extract_constraints(self, input_data: TaskUnderstandingInput) -> list[str]:
        """抽取约束"""
        constraints = []
        raw = input_data.raw_request
        context = input_data.context or ""

        combined = raw + " " + context

        # 添加已知约束
        constraints.extend(input_data.known_constraints)

        # 从请求中抽取约束
        for keyword in self.CONSTRAINT_KEYWORDS:
            pattern = rf"{keyword}[^。！？,，]+"
            matches = re.findall(pattern, combined)
            for match in matches:
                constraint = match.strip()
                if constraint and constraint not in constraints:
                    constraints.append(constraint)

        # 抽取时间约束
        time_pattern = r"(\d+[天周月年]|一周内|两周内|一个月内|尽快|立即)"
        time_matches = re.findall(time_pattern, combined)
        for match in time_matches:
            constraint = f"时间限制: {match}"
            if constraint not in constraints:
                constraints.append(constraint)

        return constraints

    def _extract_success_criteria(self, input_data: TaskUnderstandingInput, deliverables: list[str]) -> list[str]:
        """抽取成功标准"""
        criteria = []
        raw = input_data.raw_request

        # 从关键词推断成功标准
        for keyword in self.SUCCESS_KEYWORDS:
            pattern = rf"{keyword}[^。！？,，]+"
            matches = re.findall(pattern, raw)
            for match in matches:
                criterion = match.strip()
                if criterion and criterion not in criteria:
                    criteria.append(criterion)

        # 基于交付物推断默认标准
        for deliverable in deliverables:
            criteria.append(f"{deliverable}已完成")

        # 如果没有成功标准，添加默认标准
        if not criteria:
            criteria.append("任务目标已达成")

        return criteria

    def _infer_capabilities(self, input_data: TaskUnderstandingInput) -> list[str]:
        """推断所需能力"""
        capabilities = set()
        raw = input_data.raw_request

        # 从任务类型推断能力
        for task_type, caps in self.TASK_TYPE_CAPABILITIES.items():
            if task_type in raw:
                capabilities.update(caps)

        # 如果没有推断出能力，添加默认能力
        if not capabilities:
            capabilities.add("通用能力")

        return list(capabilities)

    def _infer_knowledge(self, input_data: TaskUnderstandingInput) -> list[str]:
        """推断所需知识"""
        knowledge = []
        raw = input_data.raw_request

        # 从上下文推断知识需求
        if input_data.context:
            # 简单地将上下文中的关键词作为知识需求
            words = input_data.context.split()[:5]  # 最多取前5个词
            knowledge = [w for w in words if len(w) > 1][:3]

        return knowledge

    def _infer_resources(self, input_data: TaskUnderstandingInput) -> list[str]:
        """推断所需资源"""
        resources = []

        # 从 worker_hints 推断
        if input_data.worker_hints:
            resources.extend([f"Worker: {hint}" for hint in input_data.worker_hints])

        return resources

    def _determine_risk_level(self, input_data: TaskUnderstandingInput) -> RiskLevel:
        """判定风险等级"""
        raw = input_data.raw_request.lower()
        context = (input_data.context or "").lower()
        combined = raw + " " + context

        # 检查高风险关键词
        high_risk_count = 0
        for keyword in self.HIGH_RISK_KEYWORDS:
            if keyword.lower() in combined:
                high_risk_count += 1

        # 判定风险等级
        if high_risk_count >= 3:
            return RiskLevel.CRITICAL
        elif high_risk_count >= 2:
            return RiskLevel.HIGH
        elif high_risk_count >= 1:
            return RiskLevel.MEDIUM
        else:
            return RiskLevel.LOW

    def _identify_unknowns(self, input_data: TaskUnderstandingInput) -> list[str]:
        """识别未知项"""
        unknowns = []
        raw = input_data.raw_request

        # 检查常见缺失信息
        if len(raw) < 20:
            unknowns.append("任务描述过于简短，具体范围不明确")

        # 检查是否缺少具体目标
        vague_patterns = ["做什么", "随便", "一个系统", "一个东西", "一些"]
        for pattern in vague_patterns:
            if pattern in raw:
                unknowns.append("任务目标不够具体")
                break

        # 检查是否有时间限制
        if not any(kw in raw for kw in ["天", "周", "月", "时间", "截止"]):
            unknowns.append("未指定时间限制")

        # 检查是否有已知约束
        if not input_data.known_constraints and "不能" not in raw and "必须" not in raw:
            unknowns.append("未指定约束条件")

        return unknowns

    def _decompose_subtasks(self, input_data: TaskUnderstandingInput) -> list[Subtask]:
        """拆解子任务"""
        subtasks = []
        raw = input_data.raw_request

        # 首先尝试匹配"第X步：Y"格式
        # 使用更精确的模式，匹配每个步骤
        step_pattern = r"第([一二三四五六七八九十\d]+)步[：:]\s*([^第]+?)(?=第[一二三四五六七八九十\d]+步|$)"
        matches = re.findall(step_pattern, raw)

        if matches:
            for i, (step_num, step_content) in enumerate(matches[:5]):
                step = step_content.strip().rstrip("。！？")
                if len(step) > 3:
                    subtasks.append(Subtask(
                        id=f"sub_{i+1}",
                        title=f"步骤 {i+1}",
                        objective=step[:100],
                        dependencies=[f"sub_{i}"] if i > 0 else [],
                    ))
            return subtasks

        # 检查是否有明确的步骤分隔
        steps = []
        for sep in self.STEP_SEPARATORS[:4]:  # 先检查中文分隔符
            if sep in raw:
                parts = raw.split(sep)
                if len(parts) > 1:
                    steps.extend([p.strip() for p in parts if p.strip()])
                break

        # 如果找到步骤，创建子任务
        for i, step in enumerate(steps[:5]):  # 最多5个子任务
            if len(step) > 5:  # 过滤掉太短的步骤
                subtasks.append(Subtask(
                    id=f"sub_{i+1}",
                    title=f"步骤 {i+1}",
                    objective=step[:100],  # 限制长度
                    dependencies=[f"sub_{i}"] if i > 0 else [],
                ))

        return subtasks


__all__ = ["BaselineTaskUnderstander"]