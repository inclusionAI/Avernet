"""TaskPlanner 规划编排壳(零 case 知识)+ DecomposerPort seam 委托。

对齐 plan §3.2 + 任务规划文档 uuq2tlue91q4lkal。
"""
from __future__ import annotations

from agentclaw.community.core.task.domain.models import TaskExecutionGraph, TaskNode


class TaskPlanner:
    """规划编排壳:判触发条件/读图发现目标/硬契约去重,委托 ``decomposer`` 产子节点内容。

    分层:TaskPlanner(编排壳,框架固定,零 case 知识)↔ DecomposerPort(seam,产子内容,
    stub/corp 各自实现)。DecomposerPort Protocol 定义延后(后续 task_plan/protocols.py,
    待 stub/真实规划就位)。
    """

    def __init__(self, decomposer):
        """decomposer: DecomposerPort seam(产子节点内容);首批不强类型,
        Protocol 定义延后到 task_plan/protocols.py(待 stub/真实规划就位)。"""
        self._decomposer = decomposer

    def plan(self, graph: TaskExecutionGraph) -> list[TaskNode]:
        """触发条件(规划文档):图谱有更新(新增失败节点/PLANNING 节点)AND 无派发/执行中节点
        AND 有 PLANNING 节点;不满足 → 返回 [] 空跑。
        1) 读图自发现规划目标(不依赖具体节点名):
           - FAIL: status=FAILED 且 gaps 非空 且 无分解子(叶子补救)
           - 前向/委托: status=PLANNING 的父节点
        2) 调 decomposer.decompose(graph)— seam 自洽发现 target + 产子;planner 不预选 target
        3) 硬契约兜底:纯读图去重(图上已存则不产);步进式 deps 满足才产
        4) 返回并集 list[TaskNode](不含物理执行信息)。plan 不接收外部 gaps。"""
        raise NotImplementedError
