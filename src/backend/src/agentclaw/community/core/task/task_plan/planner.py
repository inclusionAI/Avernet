"""TaskPlanner 规划编排壳(零 case 知识)+ DecomposerPort seam 委托。

对齐 plan.md §3.2 + tasks.md T3.2。
触发条件:有可规划目标(FAILED+gaps 叶 / PLANNING 父)即产子;不判 RUNNING(时序由编排核 M2 管)。
"""
from __future__ import annotations

from agentclaw.community.core.task.domain.models import (
    RelationType,
    Status,
    TaskExecutionGraph,
    TaskNode,
)
from agentclaw.community.core.task.task_plan.protocols import DecomposerPort


class TaskPlanner:
    """规划编排壳:判有无规划目标 → 委托 ``decomposer`` 产子 → 硬契约去重。

    分层:TaskPlanner(编排壳,框架固定,零 case 知识)↔ DecomposerPort(seam,产子内容,
    stub/corp 各自实现)。planner 不预选 target(decompose 自发现)。
    """

    def __init__(self, decomposer: DecomposerPort):
        self._decomposer = decomposer

    def plan(self, graph: TaskExecutionGraph) -> list[TaskNode]:
        """读图判有无可规划目标 → 调 decompose(graph) → 纯读图去重 → 返回 list[TaskNode]。

        可规划目标:① FAILED+gaps 叶(无结构子,叶子补救);② PLANNING 父(委托前向)。
        无目标 → 返回 [](不调 decompose)。plan 不接收外部 gaps,不判 RUNNING(时序由编排核管)。
        """
        if not self._has_planning_target(graph):
            return []
        nodes = self._decomposer.decompose(graph)
        existing_ids = {n.node_id for n in graph.tasks}
        return [n for n in nodes if n.node_id not in existing_ids]

    def _has_planning_target(self, graph: TaskExecutionGraph) -> bool:
        """读图自发现有无可规划目标。零 case 知识:不依赖节点名。
        目标:① 根 PENDING(无父,初始规划);② FAILED+gaps 叶(无结构子,补救);③ PLANNING 父(委托前向)。"""
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
        """节点是否有结构子(从 relations 分解树派生)。"""
        return any(
            r.src_id == node_id and r.type == RelationType.DEPENDENCY
            for r in graph.relations
        )

    def _get_parent_id(self, graph: TaskExecutionGraph, node_id: str) -> str | None:
        """节点的结构父 id(从 relations 分解树派生,单入;根返回 None)。"""
        for r in graph.relations:
            if r.dst_id == node_id and r.type == RelationType.DEPENDENCY:
                return r.src_id
        return None
