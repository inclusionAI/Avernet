"""TaskPlanner 规划编排壳(零 case 知识)+ 内置策略库(first-match-wins)。

对齐 plan.md §3.2 + §3.4。构造期注入策略池(``pool=``),内置默认 [WorkflowPlanningStrategy, GapBasedPlanningStrategy];
``set_strategies`` 仅供测试覆写,不对外暴露自定义。
Step2 改造:plan 接**显式 target_node_id**(on_fail/on_miss→失败/miss 叶,on_pass→父,on_execute 传 None→自发现根)。
返 ``PlanResult(children, has_gap, gap_detail)`` 四象限驱动编排。
"""
from __future__ import annotations

from agentclaw.community.core.task.domain.models import (
    PlanResult,
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
    """规划编排壳:解析 target → first-match-wins 选策略(graph 级 config 匹配)→ apply(graph,target) 产 PlanResult → 去重。

    分层:TaskPlanner(编排壳,框架固定,零 case 知识)↔ PlanningStrategy(引擎内置策略,
    Avernet 默认实现 gap/workflow)。策略池构造期注入(``pool=``);``set_strategies`` 仅供测试覆写。"""

    def __init__(self, graph, *, pool: list[PlanningStrategy] | None = None) -> None:
        """graph: TaskGraphService(派生查询用;策略 apply 收显式 target 经 graph 快照);
        pool: 策略池(构造期注入;省略=内置默认 [WorkflowPlanning, GapBased])。"""
        self._graph = graph
        self._strategies: list[PlanningStrategy] = list(pool) if pool is not None else [
            WorkflowPlanningStrategy(),
            GapBasedPlanningStrategy(),
        ]

    def set_strategies(self, strategies: list[PlanningStrategy]) -> None:
        """(测试覆写用)替换策略池。prod 经构造器 ``pool=`` 注入。"""
        self._strategies = list(strategies)

    async def plan(self, graph: TaskExecutionGraph, target_node_id: str | None = None) -> PlanResult:
        """解析 target → first-match-wins 选策略 → await apply(graph,target) 产 PlanResult → 去重(children)。

        target 解析:
        - ``target_node_id`` 非空 → 取该节点(由调用方保证可规划:on_fail=FAILED 叶/on_miss=PENDING miss 叶/on_pass=RUNNING 父);
        - ``target_node_id``=None → 自发现根 PENDING(初始规划;on_execute 唯一 None 调用方)。
        零 case 知识:不依赖节点名。协程化:策略 apply 在 corp 是 LLM 耗时 IO,await 不阻塞(锁内 await,同 task 串行是设计意图)。
        """
        target = self._resolve_target(graph, target_node_id)
        if target is None:
            return PlanResult(children=[], has_gap=False, gap_detail="no_target")
        for strategy in sorted(self._strategies, key=lambda r: r.priority):
            if await strategy.matches(graph):
                pr = await strategy.apply(graph, target)
                existing_ids = {n.node_id for n in graph.tasks}
                strategy_had_children = len(pr.children) > 0
                pr.children = [n for n in pr.children if n.node_id not in existing_ids]
                # 仅「策略产了子但全被去重掉(=已存在)」才视 gap 闭(无新增 actionable);
                # 策略本身返空 + has_gap=True(有 gap 拆不出 / 无规划端口)→ 保留,编排核走深度闸门 HUNG(不假 done)。
                if not pr.children and strategy_had_children:
                    pr.has_gap = False
                return pr
        return PlanResult(children=[], has_gap=False, gap_detail="no_strategy_hit")  # 兜底(不应发生:GapBased 兜底)

    def _resolve_target(self, graph: TaskExecutionGraph, target_node_id: str | None) -> TaskNode | None:
        """解析显式 target_node_id → 节点;None → 自发现根 PENDING(无父,初始规划目标)。零 case 知识。"""
        if target_node_id is not None:
            for n in graph.tasks:
                if n.node_id == target_node_id:
                    return n
            return None
        # None(on_execute):根 PENDING(无结构父)
        for n in graph.tasks:
            if n.status == Status.PENDING and not self._has_child(graph, n.node_id) and self._get_parent_id(graph, n.node_id) is None:
                return n
        return None

    def _has_child(self, graph: TaskExecutionGraph, node_id: str) -> bool:
        return any(
            r.src_id == node_id and r.type == RelationType.DEPENDENCY for r in graph.relations
        )

    def _get_parent_id(self, graph: TaskExecutionGraph, node_id: str) -> str | None:
        for r in graph.relations:
            if r.dst_id == node_id and r.type == RelationType.DEPENDENCY:
                return r.src_id
        return None
