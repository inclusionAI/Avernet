"""TaskPlanner 规划编排壳(零 case 知识)+ 内置策略库(first-match-wins)。

对齐 plan.md §3.2 + §3.4。零参构造,内置默认策略池 [WorkflowPlanningStrategy, GapBasedPlanningStrategy];
``set_strategies`` 非公开(engine 工厂方法/corp 子类注入策略池用,不对外)。
触发条件:有可规划目标(根 PENDING / FAILED+gaps 叶 / PLANNING 父)即 first-match 选策略产子。
"""
from __future__ import annotations

from agentclaw.community.core.task.domain.models import (
    RelationType,
    Status,
    TaskExecutionGraph,
    TaskNode,
)
from agentclaw.community.core.task.task_plan.strategies import (
    GapBasedPlanningStrategy,
    PlanningStrategy,
    WorkflowPlanningStrategy,
)


class TaskPlanner:
    """规划编排壳:判有无规划目标 → 对图级 execution_config first-match-wins 选策略 → apply 产子 → 去重。

    分层:TaskPlanner(编排壳,框架固定,零 case 知识)↔ PlanningStrategy(引擎内置策略,
    Avernet stub gap/workflow;corp 替换实现)。策略池内置默认;``set_strategies`` 仅供引擎
    工厂方法/corp 子类注入,不对外暴露自定义。
    """

    def __init__(self, graph) -> None:
        """graph: TaskGraphService(派生查询用;策略 apply 自发现 target 经 graph 快照)。"""
        self._graph = graph
        self._strategies: list[PlanningStrategy] = [
            WorkflowPlanningStrategy(),
            GapBasedPlanningStrategy(),
        ]

    def set_strategies(self, strategies: list[PlanningStrategy]) -> None:
        """(非公开)替换策略池。engine ``_build_planner`` 工厂方法/corp 子类注入用。"""
        self._strategies = list(strategies)

    async def plan(self, graph: TaskExecutionGraph) -> list[TaskNode]:
        """读图判有无可规划目标 → first-match-wins 选策略(graph 级 config 匹配)→ await apply 产子 → 去重。

        可规划目标:① 根 PENDING(无父,初始规划);② FAILED+gaps 叶(无结构子,补救);
        ③ PLANNING 父(委托前向)。无目标 → 返回 []。plan 不接收外部 gaps,不判 RUNNING(时序由编排核管)。
        协程化:策略 apply 在 corp 是 LLM 耗时 IO,await 不阻塞编排核(锁内 await,同 task 串行是设计意图)。
        """
        if not self._has_planning_target(graph):
            return []
        for strategy in sorted(self._strategies, key=lambda r: r.priority):
            if await strategy.matches(graph):
                nodes = await strategy.apply(graph)
                existing_ids = {n.node_id for n in graph.tasks}
                return [n for n in nodes if n.node_id not in existing_ids]
        return []  # 无策略命中(不应发生:GapBased 兜底)

    def _has_planning_target(self, graph: TaskExecutionGraph) -> bool:
        """读图自发现有无可规划目标。零 case 知识:不依赖节点名。"""
        for n in graph.tasks:
            if n.status == Status.PLANNING:
                return True
            if (
                n.status == Status.FAILED
                and n.run_info.acceptance_result is not None
                and bool(n.run_info.acceptance_result.gaps)
                and not self._has_child(graph, n.node_id)
            ):
                return True
            if (
                n.status == Status.PENDING
                and not self._has_child(graph, n.node_id)
                and self._get_parent_id(graph, n.node_id) is None
            ):
                return True  # 根 PENDING(初始规划目标)
        return False

    def _has_child(self, graph: TaskExecutionGraph, node_id: str) -> bool:
        return any(
            r.src_id == node_id and r.type == RelationType.DEPENDENCY for r in graph.relations
        )

    def _get_parent_id(self, graph: TaskExecutionGraph, node_id: str) -> str | None:
        for r in graph.relations:
            if r.dst_id == node_id and r.type == RelationType.DEPENDENCY:
                return r.src_id
        return None
