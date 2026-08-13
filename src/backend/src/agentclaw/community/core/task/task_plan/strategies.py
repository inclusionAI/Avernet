"""TaskPlanner 内置规划优化策略库(引擎自带,不开放自定义)。

对齐 plan.md §3.4(first-match-wins by priority)。策略经 ``execution_config`` 动态匹配,
类 SQL optimizer rule-based 选择:config 有 ``workflow`` → WorkflowPlanningStrategy;否则兜底 GapBased。
Avernet 默认 stub(gap 返 [];workflow 读 yaml 拓扑 stub);corp 真实 LLM 规划在 ocb 仓替换策略实现。
"""
from __future__ import annotations

from typing import Protocol

from agentclaw.community.core.task.domain.models import (
    RelationType,
    RuntimeInfo,
    Status,
    TaskExecutionGraph,
    TaskNode,
)


class PlanningStrategy(Protocol):
    """规划优化策略契约(引擎内置,first-match-wins)。"""

    rule_id: str
    priority: int

    async def matches(self, graph: TaskExecutionGraph) -> bool:
        """纯读:据图级 execution_config 判本策略是否适用(workflow/yaml 信号)。协程化:corp LLM 判定可耗 IO。"""
        ...

    async def apply(self, graph: TaskExecutionGraph) -> list[TaskNode]:
        """自发现可规划目标(FAIL 叶 / PLANNING 父 / 根 PENDING)+ 产"下一步可执行子节点"挂其下。
        返回 [] 表无可规划或 gap 已闭。协程化:corp 真实 LLM 拆解是耗时 IO,await 不阻塞。"""
        ...


class WorkflowPlanningStrategy:
    """config 有 ``workflow``(yaml)→ 加载 yaml 拓扑产出固定 dag 子节点(非 gap 拆解)。

    Avernet stub:读 ``execution_config["workflow"]``(list[str] 子节点 id / dict{parent: [children]});
    corp 真实 yaml 解析+拓扑实例化在 ocb 仓替换本类实现。
    """

    rule_id = "workflow"
    priority = 10

    async def matches(self, graph: TaskExecutionGraph) -> bool:
        cfg = graph.extend_props.get("execution_config", {}) or {}
        return cfg.get("workflow") is not None

    async def apply(self, graph: TaskExecutionGraph) -> list[TaskNode]:
        cfg = graph.extend_props.get("execution_config", {}) or {}
        wf = cfg.get("workflow")
        if not wf:
            return []
        target = _find_planning_target(graph)
        if target is None:
            return []
        task_spec = target.task_spec  # workflow 子节点复用目标 task_spec(stub)
        if isinstance(wf, list):
            return [_wf_node(nid, target.task_id, task_spec) for nid in wf]
        if isinstance(wf, dict):
            kids = wf.get(target.node_id, [])
            return [_wf_node(nid, target.task_id, task_spec) for nid in kids]
        return []


class GapBasedPlanningStrategy:
    """默认兜底:基于 gap 的任务规划。Avernet stub:返 [](不拆,根直验路径);
    corp 真实 LLM gap 计算+拆解在 ocb 仓替换本类实现。"""

    rule_id = "gap_based"
    priority = 99

    async def matches(self, graph: TaskExecutionGraph) -> bool:
        return True  # 兜底

    async def apply(self, graph: TaskExecutionGraph) -> list[TaskNode]:
        return []  # Avernet stub:不拆


def _find_planning_target(graph: TaskExecutionGraph) -> TaskNode | None:
    """读图自发现可规划目标(根 PENDING / PLANNING 父 / FAILED+gaps 叶)。零 case 知识。"""

    def _has_child(g: TaskExecutionGraph, node_id: str) -> bool:
        return any(r.src_id == node_id and r.type == RelationType.DEPENDENCY for r in g.relations)

    def _parent_id(g: TaskExecutionGraph, node_id: str) -> str | None:
        for r in g.relations:
            if r.dst_id == node_id and r.type == RelationType.DEPENDENCY:
                return r.src_id
        return None

    for n in graph.tasks:
        if n.status == Status.PLANNING:
            return n
        if (
            n.status == Status.FAILED
            and n.run_info.acceptance_result is not None
            and bool(n.run_info.acceptance_result.gaps)
            and not _has_child(graph, n.node_id)
        ):
            return n
        if n.status == Status.PENDING and not _has_child(graph, n.node_id) and _parent_id(graph, n.node_id) is None:
            return n
    return None


def _wf_node(node_id: str, task_id: str, task_spec) -> TaskNode:
    """构造 workflow 子节点(PENDING,空 run_info)。"""
    return TaskNode(
        node_id=node_id,
        task_id=task_id,
        status=Status.PENDING,
        task_spec=task_spec,
        run_info=RuntimeInfo(),
        node_run_graph=None,  # type: ignore[arg-type]
    )
