"""任务回投契约(供外部 bot workflow / bcn 协作群 PUSH 回投)。对齐 plan §3.5.2 + 执行模块文档。"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from agentclaw.community.core.task.domain.models import TaskCallbackData


@runtime_checkable
class TaskLoopCallbackProtocol(Protocol):
    """执行实体(bot workflow / bcn 协作群)PUSH 回投入口,对接框架
    update_task_node_info(经编排核 on_report)。"""

    async def start_run(self, data: TaskCallbackData) -> None:
        """任务开始执行(可选进度信号)。协程化:回投链路 async,await 不阻塞调用方。"""
        ...

    async def report_result(self, data: TaskCallbackData) -> None:
        """任务完成或失败(success/data or fail_detail):适配层组装 TaskNodePatch
        → 编排核 on_report(await) → graph.update_task_node_info → 按 verdict 翻态/传播/补救。
        协程化:on_report async,回投不阻塞调用方(任务执行是耗时任务,回投驱动编排核 async)。"""
        ...

    async def ingest(self, data: TaskCallbackData) -> None:
        """仅落回投审计(``task_callback``),不推进编排核。供事件/工作流级回投(ClawMind/BCN)用:
        其 run_id/workflow_id 不对应框架节点,走 ``start_run``/``report_result`` 会 NodeNotFoundError。"""
        ...

    async def ingest_parse_error(self, raw: dict, error: str) -> None:
        """回调解析失败兜底落库:仅写 ``exec_error`` 和原始上报数据,不推进编排核。"""
        ...
