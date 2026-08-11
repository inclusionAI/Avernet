"""P3 — TaskPlanner 规划编排壳 (v4.2 / 方案 B).

只读 graph 自算 gap → 发现规划目标 → 把"产哪些节点"委托给 ``DecomposerPort`` 策略。
框架只提供**机制**(读图发现目标、优先级 MISS>FAIL>前向、硬契约①②、步进式、去重),
**不含任何具体任务的节点结构知识**(零 case 知识)。任何具体节点的产出只能由
decomposer 策略产出 (stub/LLM/SKLL),绝不写死在本模块。

设计 v4.2: ``plan.md`` §3.2 (DecomposerPort 作为默认 GapBasedPlanningRule 的内容 delegate)。

契约 (v4, 不变):
- ``plan(graph) -> list[TaskNode]``: 只读全图,**不接收外部 gaps**。
- 产 ``list[TaskNode]`` (status=PENDING, run_info 空,不含物理执行、不决定谁做)。
- **步进式**: 每次只产"下一步可执行"的节点,不一次铺满。
- **硬契约①**: 产的每个子其父语义已就绪可委托 (被 decompose 的目标节点本身就是父)。
- **硬契约②**: 无状态纯读图;图上已存在 (有分解子) 则不重复对该目标 decompose (去重)。

三种目标 (plan 读图自发现,优先级 MISS > FAIL > 前向):
  1. MISS: ``extend_props.miss_events`` 非空的节点 → decompose 该节点。
  2. FAIL (model B): ``FAILED`` + ``acceptance_result.gaps`` 非空且无分解子 → decompose 该节点
     产补救子挂**该节点**下。
  3. 前向: 任意"可分解叶" (无分解子且 depends_on 已满足) → decompose 产出下一层。

生产级分解 (按 goal/AC 语义) 走 LLM/SKILL (corp);Avernet 用注入的 stub/case decomposer。
可经 ``OptimizerRule`` 包策略:``GapBasedPlanningRule`` 委托 ``DecomposerPort`` (见 optimizer.py)。
"""
from __future__ import annotations

from agentclaw.community.core.task_goal_driven.models import (
    Status,
    TaskExecutionGraph,
    TaskNode,
)
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agentclaw.community.core.task_goal_driven.protocols import DecomposerPort


class TaskPlanner:
    """规划编排壳:读图发现目标 + 委托 DecomposerPort 产内容 + 硬契约兜底."""

    def __init__(self, decomposer: "DecomposerPort") -> None:
        # decomposer 必填 (无默认): 框架本身无分解内容,必须注入策略。
        self._decompose = decomposer

    def plan(self, graph: TaskExecutionGraph) -> list[TaskNode]:
        """读图 → MISS>FAIL>前向 选一个目标 → 委托 decomposer 产下一层;无目标返回 []."""
        tasks = graph.tasks

        # 1) MISS 递归拆解: 最具体 (engine 刚写 miss_events),优先
        miss = [n for n in tasks if n.run_info.extend_props.get("miss_events")]
        if miss:
            return self._dedup(graph, self._decompose.decompose(miss[0], graph))

        # 2) FAIL 补救 (model B): FAILED+gaps 且尚无分解子 (去重) → 针对该节点产子挂它下
        failed = [n for n in tasks
                  if n.status == Status.FAILED
                  and n.run_info.acceptance_result
                  and n.run_info.acceptance_result.gaps
                  and not _has_spawned_children(graph, n.node_id)]
        if failed:
            # 对第一个 FAIL 目标委托产补救 (步进式: 一次一个目标)
            return self._dedup(graph, self._decompose.decompose(failed[0], graph))

        # 3) 前向: 第一个"可分解叶" (无分解子 + depends_on 满足;根无子也算可分解)
        forward_target = self._forward_target(graph)
        if forward_target is not None:
            return self._dedup(graph, self._decompose.decompose(forward_target, graph))

        return []

    # ------------------------------------------------------------------
    # 前向目标选择 (机制,无内容): 无分解子且 (根 or deps 满足) 的节点
    # ------------------------------------------------------------------

    @staticmethod
    def _forward_target(graph: TaskExecutionGraph) -> TaskNode | None:
        """选一个可分解叶: 无分解子,且 (无依赖=根 or 全依赖已 DONE)."""
        for n in graph.tasks:
            if _has_spawned_children(graph, n.node_id):
                continue
            # deps 满足: 空依赖 (根) 或所有 depended 放节点已 DONE
            if (not n.depends_on
                    or all(_node_done(graph, pid) for pid in n.depends_on)):
                return n
        return None

    # ------------------------------------------------------------------
    # 去重 (硬契约②): 图上已存的 node_id 不重复产
    # ------------------------------------------------------------------

    @staticmethod
    def _dedup(graph: TaskExecutionGraph, produced: list[TaskNode]) -> list[TaskNode]:
        existing = {n.node_id for n in graph.tasks}
        return [n for n in produced if n.node_id not in existing]


# ============================================================================
# 帮助函数 (机制,无内容)
# ============================================================================


def _has_spawned_children(graph: TaskExecutionGraph, node_id: str) -> bool:
    """节点是否已有分解子 (spawned children).

    结构派生: 图上有节点的 depends_on 恰好为 [node_id] (单父 ≈ 分解子;
    数据消费方有多父,不在此列,放行可被 _forward_target 选中再 decompose)。
    用于去重与"委托中"判定。
    """
    return any(n.depends_on == [node_id] for n in graph.tasks)


def _node_done(graph: TaskExecutionGraph, node_id: str) -> bool:
    """节点是否 DONE (供前向 deps 满足判定)."""
    return next((n.status == Status.DONE for n in graph.tasks if n.node_id == node_id), False)
