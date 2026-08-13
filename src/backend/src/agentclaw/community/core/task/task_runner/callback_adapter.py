"""回投适配层:TaskCallbackData → TaskNodePatch → ExecutionEngine.on_report。

对齐 plan.md §3.5.2。TaskLoopCallback 实现类(实现 api/task/task_loop_callback.py Protocol)并入此模块。
协程化:report_result/start_run 为 async(on_report 链路 async),await 不阻塞回投调用方。
"""
from __future__ import annotations

from agentclaw.community.core.task.domain.models import (
    AcceptanceResult,
    AcceptanceVerdict,
    TaskCallbackData,
    TaskNodePatch,
)


class CallbackAdapter:
    """把执行实体回投的 TaskCallbackData 组装成 TaskNodePatch。

    Avernet stub:loop_task_id 格式 = f"{task_id}::{node_id}"(start_run 时记录的格式);
    真实 workflow 的 loop_task_id 映射需 corp adapter 落地。
    result.success/data→acceptance_result(PASS/FAIL)+ output;fail_detail→gaps/extend_props。
    """

    def adapt(self, data: TaskCallbackData) -> TaskNodePatch:
        task_id, node_id = data.loop_task_id.split("::", 1)
        success = bool(data.result.get("success", False))
        out = data.result.get("data")
        fail_detail = data.result.get("fail_detail")
        if success:
            acceptance = AcceptanceResult(verdict=AcceptanceVerdict.PASS, acceptances_metric=["exec_ok"])
        else:
            acceptance = AcceptanceResult(
                verdict=AcceptanceVerdict.FAIL,
                gaps=[fail_detail] if fail_detail else ["unknown_gap"],
            )
        return TaskNodePatch(
            task_id=task_id,
            node_id=node_id,
            output_patch={"data": out} if out is not None else None,
            acceptance_result=acceptance,
            extend_props_patch={"fail_detail": fail_detail} if fail_detail else None,
        )


class TaskLoopCallback:
    """供执行实体(bot workflow / bcn 协作群)PUSH 回投,对接框架 update_task_node_info(经编排核 on_report)。
    实现 api/task/task_loop_callback.py 的 TaskLoopCallbackProtocol。

    协程化:report_result/start_run 为 async,经 await engine.on_report 驱动编排核(async 链路),
    不阻塞回投调用方(HTTP 适配层/外部 bot workflow)。"""

    def __init__(self, adapter: CallbackAdapter, engine) -> None:
        """adapter: CallbackAdapter;engine: ExecutionEngine(on_report async 入口)。"""
        self._adapter = adapter
        self._engine = engine

    async def start_run(self, data: TaskCallbackData) -> None:
        """任务开始执行(可选进度信号,Avernet stub:记录不驱动)。协程化:与 report_result 链路一致。"""
        return None

    async def report_result(self, data: TaskCallbackData) -> None:
        """任务完成或失败:适配层组装 TaskNodePatch → 编排核 on_report(await) → graph.update_task_node_info → 翻态/传播/补救。
        协程化:on_report 是 async,await 不阻塞回投调用方。"""
        patch = self._adapter.adapt(data)
        await self._engine.on_report(patch)
