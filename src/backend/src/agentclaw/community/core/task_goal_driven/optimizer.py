"""P3 支撑 — 可插拔优化策略契约 (Unified Optimizer Contract).

设计 v4: ``design-review.md`` §2.6、§5 决策 13 (可插拔策略)。

将 Planner/Dispatcher 的内部实现做成可插拔的优化策略 (像 SQL 逻辑执行计划优化器):
- 默认实现 (GAP 规划 / 搜推分发) 只是诸多优化策略中的默认一种。
- 后续可扩展自定义 ``OptimizerRule`` (LLM 规划、线上真实搜推等),通过 composition root 装配。
- **编排骨 (ExecutionEngine) / facade / 模型零改动** — 只替换接缝内的规则实现。

泛型 ``OptimizerRule[InputT, ResultT]`` (统一编译器规则契约):
- ``rule_id``: 规则标识 (composition root 按需选择)。
- ``priority``: 优先级 (first-match-wins,数字小优先)。
- ``matches``: 判断当前输入是否匹配此规则 (不产生副作用)。
- ``apply``: 执行规则,产结果 (可包含副作用,如 dispatcher patch RUNNING)。

M1: 先落 ``GapBasedPlanningRule`` 骨架 (整体承载 plan 逻辑);``SearchBasedDispatchRule``
留骨架 (M2 落地)。
"""
from __future__ import annotations
from typing import TYPE_CHECKING

from typing import Generic, Protocol, TypeVar

from agentclaw.community.core.task_goal_driven.models import (
    TaskExecutionGraph,
    TaskNode,
)

if TYPE_CHECKING:
    from agentclaw.community.core.task_goal_driven.planner import TaskPlanner
    from agentclaw.community.core.task_goal_driven.protocols import DecomposerPort

PlanT = TypeVar("PlanT")
ResultT = TypeVar("ResultT")


class OptimizerRule(Protocol, Generic[PlanT, ResultT]):
    """统一编译器规则契约 (可插拔优化策略).

    一整条策略 = 一条规则。``Optimizer`` 按 priority first-match-wins 驱动。
    """

    rule_id: str
    priority: int

    def matches(self, graph: TaskExecutionGraph, input_: PlanT) -> bool:
        """判断当前输入是否匹配此规则 (纯读,不产生副作用)."""
        ...

    def apply(self, graph: TaskExecutionGraph, input_: PlanT) -> ResultT:
        """执行规则,产结果 (可包含副作用;如 dispatcher 派发后 patch RUNNING)."""
        ...


class Optimizer(Generic[PlanT, ResultT]):
    """规则驱动器: 按 priority first-match-wins 选择第一条匹配的规则执行.

    composition root 装配规则列表 (有序);``optimize`` 返回第一条匹配规则的 ``apply``
    结果,无匹配返回 ``default``。
    """

    def __init__(self, rules: list[OptimizerRule[PlanT, ResultT]]) -> None:
        # 按 priority 升序排列 (数字小优先)
        self._rules = sorted(rules, key=lambda r: r.priority)

    def optimize(self, graph: TaskExecutionGraph, input_: PlanT,
                  default: ResultT | None = None) -> ResultT | None:
        """first-match-wins: 第一条 ``matches`` 的规则执行;无匹配返回 default."""
        for rule in self._rules:
            if rule.matches(graph, input_):
                return rule.apply(graph, input_)
        return default


# ============================================================================
# 默认规划策略骨架 (GapBasedPlanningRule)
# 规则版参考实现;整体承载 plan 逻辑 (等价于 TaskPlanner.plan 的四种触发)。
# M1: TaskPlanner 作为 thin facade 委托此规则 (composition root 装配)。
# ============================================================================


class GapBasedPlanningRule:
    """默认规划策略:基于 GAP 的步进式任务规划 (v4.2: 委托 DecomposerPort).

    ``rule_id`` = ``"gap_based_planning"``, ``priority`` = 100 (默认)。
    ``matches``: 总是 True (默认策略,fallback)。
    ``apply``: 读图发现规划目标 → 委托 ``DecomposerPort`` 产内容 → 硬契约兜底。

    本规则承载**机制** (规划壳);**内容** (产哪些节点) 由注入的 decomposer 策略决定
    (Avernet stub/singlebox,corp LLM/SKILL)。composition root 装配 decomposer 与 planner。
    """

    rule_id: str = "gap_based_planning"
    priority: int = 100

    def __init__(self, planner: "TaskPlanner | None" = None,
                 decomposer: "DecomposerPort | None" = None) -> None:
        self._planner = planner
        self._decomposer = decomposer

    def matches(self, graph: TaskExecutionGraph, input_: TaskExecutionGraph) -> bool:
        return True  # 默认策略总是匹配 (fallback)

    def apply(self, graph: TaskExecutionGraph, input_: TaskExecutionGraph) -> list[TaskNode]:
        if self._planner is None:
            from agentclaw.community.core.task_goal_driven.planner import TaskPlanner
            if self._decomposer is None:
                raise ValueError(
                    "GapBasedPlanningRule 需要注入 DecomposerPort (框架零 case 知识); "
                    "composition root 必须装配一个 decomposer 策略")
            self._planner = TaskPlanner(self._decomposer)
        return self._planner.plan(graph)


# ============================================================================
# 默认分发策略骨架 (SearchBasedDispatchRule) — M2 落地
# ============================================================================


class SearchBasedDispatchRule:
    """默认分发策略:基于搜推的任务分发 (M2 落地).

    ``rule_id`` = ``"search_based_dispatch"``, ``priority`` = 100 (默认)。
    M1 仅骨架;M2 注入 BotDiscoverPort + TaskExecution 完成搜推 4 态 (HIT_SINGLE /
    HIT_GROUP / HIT_MULTI_BOTS / MISS) 的 executor 选择与动态拉群。
    """

    rule_id: str = "search_based_dispatch"
    priority: int = 100

    def matches(self, graph: TaskExecutionGraph, input_: list[TaskNode]) -> bool:
        return True

    def apply(self, graph: TaskExecutionGraph, input_: list[TaskNode]) -> list:
        raise NotImplementedError("SearchBasedDispatchRule M2 待落地")
