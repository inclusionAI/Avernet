"""
Task Requests Fixtures

M3: Task Understanding Engine

提供典型的任务请求样例，供单元测试和集成测试复用。

原则：
- 样例足够典型
- 样例足够小
- 可复用
"""

from __future__ import annotations

from src.domain.models.task_understanding_input import TaskUnderstandingInput


# =============================================================================
# 简单请求样例
# =============================================================================

REQUEST_SIMPLE_DESIGN = "帮我设计一个系统架构方案"
REQUEST_SIMPLE_RESEARCH = "进行技术调研并输出报告"
REQUEST_SIMPLE_DEVELOP = "开发一个用户登录功能"
REQUEST_SIMPLE_DOCUMENT = "编写API文档"


# =============================================================================
# 复杂请求样例
# =============================================================================

REQUEST_MULTI_STEP = """
我需要组建一个团队来完成技术调研，然后设计方案，最后输出调研报告和设计文档。
第一步：调研现有技术方案。
第二步：设计架构方案。
第三步：输出最终报告。
"""

REQUEST_WITH_CONSTRAINTS = """
帮我设计一个Python Web API系统，要求：
1. 必须使用FastAPI框架
2. 不能使用外部API
3. 必须在两周内完成
4. 需要输出设计文档和测试报告
"""

REQUEST_WITH_CONTEXT = "完成这个项目"


# =============================================================================
# 模糊请求样例
# =============================================================================

REQUEST_VAGUE_SHORT = "做一个系统"
REQUEST_VAGUE_VERY = "帮我做一下"
REQUEST_VAGUE_EMPTY = "随便做点什么"


# =============================================================================
# 高风险请求样例
# =============================================================================

REQUEST_HIGH_RISK_PRODUCTION = "直接修改生产数据库中的用户数据"
REQUEST_HIGH_RISK_EXTERNAL = "发送外部邮件并访问外部API"
REQUEST_HIGH_RISK_SENSITIVE = "删除生产环境的敏感数据"
REQUEST_HIGH_RISK_PAYMENT = "处理用户支付信息并修改权限"


# =============================================================================
# 明确请求样例（低 unknowns）
# =============================================================================

REQUEST_CLEAR_FULL = """
设计一个Python Web API系统，使用FastAPI框架，实现用户认证和授权功能。
需要输出：
1. API设计文档
2. 测试报告
3. 部署方案

时间限制：一周内完成
约束条件：不能使用外部服务，必须在Linux上运行
"""


# =============================================================================
# 工厂函数
# =============================================================================

def get_simple_design_input() -> TaskUnderstandingInput:
    """获取简单设计请求输入"""
    return TaskUnderstandingInput(
        raw_request=REQUEST_SIMPLE_DESIGN,
    )


def get_simple_research_input() -> TaskUnderstandingInput:
    """获取简单调研请求输入"""
    return TaskUnderstandingInput(
        raw_request=REQUEST_SIMPLE_RESEARCH,
    )


def get_multi_step_input() -> TaskUnderstandingInput:
    """获取多步骤请求输入"""
    return TaskUnderstandingInput(
        raw_request=REQUEST_MULTI_STEP.strip(),
    )


def get_input_with_constraints() -> TaskUnderstandingInput:
    """获取带约束的请求输入"""
    return TaskUnderstandingInput(
        raw_request=REQUEST_WITH_CONSTRAINTS.strip(),
        known_constraints=["需要在两周内完成"],
    )


def get_input_with_context() -> TaskUnderstandingInput:
    """获取带上下文的请求输入"""
    return TaskUnderstandingInput(
        raw_request=REQUEST_WITH_CONTEXT,
        context="这是一个内部技术调研项目，需要输出调研报告",
    )


def get_vague_input() -> TaskUnderstandingInput:
    """获取模糊请求输入"""
    return TaskUnderstandingInput(
        raw_request=REQUEST_VAGUE_SHORT,
    )


def get_high_risk_production_input() -> TaskUnderstandingInput:
    """获取高风险生产环境请求输入"""
    return TaskUnderstandingInput(
        raw_request=REQUEST_HIGH_RISK_PRODUCTION,
    )


def get_high_risk_external_input() -> TaskUnderstandingInput:
    """获取高风险外部访问请求输入"""
    return TaskUnderstandingInput(
        raw_request=REQUEST_HIGH_RISK_EXTERNAL,
    )


def get_clear_defined_input() -> TaskUnderstandingInput:
    """获取明确定义的请求输入"""
    return TaskUnderstandingInput(
        raw_request=REQUEST_CLEAR_FULL.strip(),
    )


def get_input_with_worker_hints() -> TaskUnderstandingInput:
    """获取带 worker hints 的请求输入"""
    return TaskUnderstandingInput(
        raw_request="完成调研任务",
        worker_hints=["bot_researcher_001", "bot_analyst_001"],
    )


def get_complete_task_input() -> TaskUnderstandingInput:
    """获取完整任务请求输入样例"""
    return TaskUnderstandingInput(
        raw_request=REQUEST_CLEAR_FULL.strip(),
        context="这是一个内部项目，由技术团队负责",
        known_constraints=["代码审查必须通过", "需要安全审计"],
        worker_hints=["bot_architect_001"],
        metadata={"priority": "high", "project_id": "proj_001"},
    )


__all__ = [
    # Simple samples
    "REQUEST_SIMPLE_DESIGN",
    "REQUEST_SIMPLE_RESEARCH",
    "REQUEST_SIMPLE_DEVELOP",
    "REQUEST_SIMPLE_DOCUMENT",
    # Complex samples
    "REQUEST_MULTI_STEP",
    "REQUEST_WITH_CONSTRAINTS",
    "REQUEST_WITH_CONTEXT",
    # Vague samples
    "REQUEST_VAGUE_SHORT",
    "REQUEST_VAGUE_VERY",
    "REQUEST_VAGUE_EMPTY",
    # High-risk samples
    "REQUEST_HIGH_RISK_PRODUCTION",
    "REQUEST_HIGH_RISK_EXTERNAL",
    "REQUEST_HIGH_RISK_SENSITIVE",
    "REQUEST_HIGH_RISK_PAYMENT",
    # Clear samples
    "REQUEST_CLEAR_FULL",
    # Factory functions
    "get_simple_design_input",
    "get_simple_research_input",
    "get_multi_step_input",
    "get_input_with_constraints",
    "get_input_with_context",
    "get_vague_input",
    "get_high_risk_production_input",
    "get_high_risk_external_input",
    "get_clear_defined_input",
    "get_input_with_worker_hints",
    "get_complete_task_input",
]