"""任务回投契约(供外部 bot workflow / bcn 协作群 PUSH 回投)。对齐 plan §3.5.2 + 执行模块文档。"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from agentclaw.community.core.task.domain.models import TaskCallbackData


@runtime_checkable
class TaskLoopCallbackProtocol(Protocol):
    """执行实体(bot workflow / bcn 协作群)PUSH 回投入口,对接框架
    update_task_node_info(经编排核 on_report)。"""

    def start_run(self, data: TaskCallbackData) -> None:
        """任务开始执行(可选进度信号)。"""
        ...

    def report_result(self, data: TaskCallbackData) -> None:
        """任务完成或失败(success/data or fail_detail):适配层组装 TaskNodePatch
        → 编排核 on_report → graph.update_task_node_info → 按 verdict 翻态/传播/补救。"""
        ...
