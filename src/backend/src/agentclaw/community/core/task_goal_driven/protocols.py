"""P2/P3 支撑 — ExecutionEngine 依赖反转 seam + 规划内容 delegate (Protocols).

ExecutionEngine 依赖 **抽象**,不依赖 P3/P4/P5 的具体实现。M1 (P0–P3) 用测试 doubles
注入;M2 (P4/P5) 注入真 TaskDispatcher / TaskRunner。core 编排核可独立单测
(无执行主体),遵循 AGENTS.md "core transport-agnostic / 契约先行"。

契约来源:``plan.md`` §2.0–2.5 (v4.2 / 方案 B)。

v4.2 变更:
- ``PlannerPort`` 单方法 ``plan(graph)``,不接收外部 gaps。
- 新增 ``DecomposerPort``:规划**内容** delegate seam (默认 GapBasedPlanningRule 委托它);
  框架零 case 知识,分解内容由策略产出 (Avernet stub/singlebox,corp LLM/SKILL)。
  命名对齐旧 ``core/task`` 同名 seam。
- MISS 信号走 ``extend_props.miss_events``,plan 读图自发现 (非外部 gaps 参数)。
"""
from __future__ import annotations

from typing import Protocol

from agentclaw.community.core.task_goal_driven.models import (
    DispatchOutcome,
    TaskExecutionGraph,
    TaskNode,
)


class PlannerPort(Protocol):
    """规划 seam:读图发现规划目标 -> 委托 DecomposerPort 产新节点 (v4.2 编排壳).

    契约: ``plan(graph) -> list[TaskNode]`` — 单方法,不接收外部 gaps。
    三种目标 (plan 读图自发现),优先级 MISS > FAIL > 前向:
      1. MISS: ``extend_props.miss_events`` 非空 → 委托 decompose 该节点。
      2. FAIL (model B): ``FAILED`` + ``acceptance_result.gaps`` 非空无子 → 委托 decompose。
      3. 前向: 可分解叶 (无分解子 + depends_on 满足;根无子也算) → 委托 decompose。
    硬契约: 产的子其父语义已就绪可委托;纯读图去重。
    """

    def plan(self, graph: TaskExecutionGraph) -> list[TaskNode]:
        """产 ``list[TaskNode]`` (status=PENDING,run_info 空);不含物理执行."""
        ...


class DecomposerPort(Protocol):
    """规划内容 delegate seam (方案 B / v4.2).

    真正的分解智能:对单个规划目标节点产"下一步可执行的子节点"(挂该节点下;
    status=PENDING, run_info 空)。触发语义由 ``TaskPlanner`` 决定 (node 是
    MISS/FAIL 目标或前向可分解叶);本方法只产**内容**,不管步进/去重/硬契约
    (由 ``TaskPlanner._dedup`` 兜底)。

    返回 ``[]`` 表示该节点不可再分解 (终态候选叶)。

    默认实现:
      - Avernet (singlebox/测试): 注入 stub/case decomposer 返回固定节点。
      - corp ``ocb``: LLM/SKILL 按 goal/AC 语义分解 (Avernet 不含,红线)。
    """

    def decompose(self, node: TaskNode, graph: TaskExecutionGraph) -> list[TaskNode]:
        """对规划目标节点产下一步子节点 (挂该 node 下);不可再分返回 []."""
        ...


class DispatcherPort(Protocol):
    """分发 seam:无状态节点函数 (决定派发目标,不发起执行;执行交 TaskRunner)."""
    def dispatch(self, to_do_list: list[TaskNode]) -> list[DispatchOutcome]:
        """per node: search -> 落派发目标到 RuntimeInfo + 置 RUNNING -> 触发 TaskRunner.start_run;
        MISS 仅回传 outcome 交引擎."""
        ...


class BbsExecutorPort(Protocol):
    """BBS 接力执行 seam (``plan.md`` §3.5).人工确认升 BBS 后由 engine 调 TaskRunner."""
    def run_bbs(self, node: TaskNode, output_projection: dict) -> str:
        """任务广场 lease/claim 接力;返回 runtime_task_id."""
        ...


# 运行时引用 (避免循环导入仅在 type-check 时) — TaskPlanner 构造签名用
__all__ = [
    "PlannerPort",
    "DecomposerPort",
    "DispatcherPort",
    "BbsExecutorPort",
]
