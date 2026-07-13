"""
Planning Inputs Fixtures

M4: Research & Planning Engine

提供典型的规划输入样例，供单元测试和集成测试复用。

原则：
- 样例足够典型
- 样例足够小
- 可复用
"""

from __future__ import annotations

from src.domain.models.task_spec import TaskSpec, RiskLevel, Subtask
from src.domain.models.task_understanding_result import UnderstandingWarning


# =============================================================================
# TaskSpec 样例
# =============================================================================

def get_simple_design_task_spec() -> TaskSpec:
    """获取简单设计任务规格"""
    return TaskSpec(
        id="tsk_design_001",
        goal="设计一个系统架构方案",
        deliverables=["架构设计文档", "技术选型报告"],
        constraints=["使用Python", "预算有限"],
        success_criteria=["设计文档完成", "技术选型确定"],
        required_capabilities=["系统设计", "架构设计"],
        required_knowledge=["分布式系统", "微服务"],
        required_resources=["设计工具", "文档平台"],
        risk_level=RiskLevel.LOW,
        unknowns=[],
        subtasks=[],
        source_prompt="帮我设计一个系统架构方案",
    )


def get_multi_step_task_spec() -> TaskSpec:
    """获取多步骤任务规格"""
    return TaskSpec(
        id="tsk_research_001",
        goal="完成技术调研并输出报告",
        deliverables=["调研报告", "方案对比文档"],
        constraints=["一周内完成", "不超过3人"],
        success_criteria=["调研报告完成", "方案对比完成"],
        required_capabilities=["信息检索", "分析能力", "文档编写"],
        required_knowledge=["相关技术领域"],
        required_resources=["调研资料", "文档工具"],
        risk_level=RiskLevel.LOW,
        unknowns=["具体调研范围"],
        subtasks=[
            Subtask(
                id="sub_1",
                title="调研现有方案",
                objective="调研现有技术方案",
                dependencies=[],
            ),
            Subtask(
                id="sub_2",
                title="分析对比",
                objective="分析和对比各方案",
                dependencies=["sub_1"],
            ),
            Subtask(
                id="sub_3",
                title="输出报告",
                objective="输出调研报告",
                dependencies=["sub_2"],
            ),
        ],
        source_prompt="完成技术调研并输出报告",
    )


def get_high_risk_task_spec() -> TaskSpec:
    """获取高风险任务规格"""
    return TaskSpec(
        id="tsk_prod_001",
        goal="修改生产数据库配置",
        deliverables=["配置变更报告", "验证结果"],
        constraints=["必须审批", "需要回滚方案"],
        success_criteria=["配置更新成功", "验证通过"],
        required_capabilities=["数据库管理", "运维能力"],
        required_knowledge=["生产环境", "数据库配置"],
        required_resources=["生产数据库访问权限"],
        risk_level=RiskLevel.HIGH,
        unknowns=["审批流程"],
        subtasks=[
            Subtask(
                id="sub_1",
                title="准备变更",
                objective="准备变更方案和回滚计划",
                dependencies=[],
            ),
            Subtask(
                id="sub_2",
                title="执行变更",
                objective="执行数据库配置变更",
                dependencies=["sub_1"],
            ),
        ],
        source_prompt="修改生产数据库配置",
    )


def get_vague_task_spec() -> TaskSpec:
    """获取模糊任务规格"""
    return TaskSpec(
        id="tsk_vague_001",
        goal="做一个系统",
        deliverables=["任务产出"],
        constraints=[],
        success_criteria=["任务目标已达成"],
        required_capabilities=["通用能力"],
        required_knowledge=[],
        required_resources=[],
        risk_level=RiskLevel.LOW,
        unknowns=["任务描述过于简短，具体范围不明确", "未指定时间限制", "未指定约束条件"],
        subtasks=[],
        source_prompt="做一个系统",
    )


def get_team_composition_task_spec() -> TaskSpec:
    """获取组队任务规格"""
    return TaskSpec(
        id="tsk_team_001",
        goal="组建团队完成项目",
        deliverables=["团队配置", "执行计划"],
        constraints=["需要human参与", "预算有限"],
        success_criteria=["团队组建完成", "计划制定完成"],
        required_capabilities=["团队协调", "项目管理", "技术能力"],
        required_knowledge=["项目背景"],
        required_resources=["人力资源"],
        risk_level=RiskLevel.MEDIUM,
        unknowns=["具体人员安排"],
        subtasks=[
            Subtask(
                id="sub_1",
                title="需求分析",
                objective="分析项目需求",
                dependencies=[],
            ),
            Subtask(
                id="sub_2",
                title="人员选拔",
                objective="选拔合适的团队成员",
                dependencies=["sub_1"],
            ),
            Subtask(
                id="sub_3",
                title="组建团队",
                objective="完成团队组建",
                dependencies=["sub_2"],
            ),
        ],
        source_prompt="组建团队完成项目",
    )


# =============================================================================
# Understanding Warnings 样例
# =============================================================================

def get_understanding_warnings() -> list[UnderstandingWarning]:
    """获取理解警告样例"""
    return [
        UnderstandingWarning(
            field="unknowns",
            message="发现 2 个未知项需要澄清",
            suggestion="请提供更多细节以获得更准确的任务规格",
        ),
    ]


__all__ = [
    # TaskSpec factories
    "get_simple_design_task_spec",
    "get_multi_step_task_spec",
    "get_high_risk_task_spec",
    "get_vague_task_spec",
    "get_team_composition_task_spec",
    # Warning factories
    "get_understanding_warnings",
]