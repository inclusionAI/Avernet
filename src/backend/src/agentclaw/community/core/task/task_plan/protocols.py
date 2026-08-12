"""TaskPlan seam:DecomposerPort 分解策略 Protocol。对齐 plan.md §3.2 + tasks.md T3.1。

非领域实体,模块层接缝;与 TaskPlanner 委托关系。Avernet stub/singlebox;corp plan_bot/LLM SKILL。
"""
from __future__ import annotations

from typing import Protocol

from agentclaw.community.core.task.domain.models import TaskExecutionGraph, TaskNode


class DecomposerPort(Protocol):
    """分解策略 seam:读图自发现规划目标(FAIL 叶 / PLANNING 父)并产"下一步可执行的子节点"。

    产子契约:status=PENDING,run_info 空,task_id 已填,node_run_graph 指向所属图。
    返回 [] 表无可规划目标(decompose(root)==[] 的判断属实现侧:stub/corp 各自负责)。
    """

    def decompose(self, graph: TaskExecutionGraph) -> list[TaskNode]:
        """读图自发现 target + 产子(挂该 target 下);target-finding 由本 seam 自洽。"""
        ...
