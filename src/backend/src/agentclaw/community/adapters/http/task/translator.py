"""回调请求边缘翻译:羽雀 schema → SSOT TaskCallbackData(+disposition)。

SSOT TaskCallbackData 不扩;ext_info/goal/未登记 str id 塞进 result["_ext_info"],
由 CallbackAdapter.adapt/adapt_start 折进 extend_props_patch。零 case:仅消费 schema 字段
+ loop_task_id/node_id/workflow_source,无节点名字面量。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from agentclaw.community.core.errors import CallbackCorrelationError
from agentclaw.community.core.task.domain.models import TaskCallbackData
from agentclaw.community.core.task.task_runner.callback_correlation import (
    CallbackCorrelationRegistry,
)

from .schemas import TaskCallbackRequest, TaskNodeCallbackRequest

_SOURCE_TO_TYPE = {"claw_mind": "single_bot", "bcn": "bcn_coop_group"}


@dataclass(frozen=True)
class TranslatedCallback:
    disposition: Literal["start", "result"]
    data: TaskCallbackData


def translate(
    req: TaskCallbackRequest,
    disposition: Literal["start", "result"],
    registry: CallbackCorrelationRegistry,
) -> TranslatedCallback:
    source = req.workflow_source
    workflow_type = _SOURCE_TO_TYPE[source]

    # loop_task_id 解析:回声 > node 直拼 > registry > CallbackCorrelationError
    loop_task_id = req.loop_task_id
    if loop_task_id is None:
        if isinstance(req, TaskNodeCallbackRequest):
            loop_task_id = f"{req.task_id}::{req.node_id}"
        else:
            rec = registry.resolve(source, req.workflow_instance_id)
            if rec is None:
                raise CallbackCorrelationError(
                    f"task-level callback unregistered: {source}/{req.workflow_instance_id}"
                )
            loop_task_id = rec.loop_task_id

    # registry 取 SSOT int id(未登记 node 级回退 0)
    rec = registry.resolve(source, req.workflow_instance_id)
    wf_id_int = rec.workflow_id if rec is not None else 0
    inst_id_int = rec.instance_id if rec is not None else 0

    # result 折叠
    result: dict = {"success": req.is_success}
    if req.output is not None:
        result["data"] = req.output
    if req.failed_info is not None:
        result["fail_detail"] = req.failed_info

    # ext_info/goal/未登记 str id → result["_ext_info"]
    ext: dict = dict(req.ext_info or {})
    if req.goal is not None:
        ext["_callback_goal"] = req.goal
    if rec is None:
        ext.setdefault("_workflow_id_str", req.workflow_id)
        ext.setdefault("_instance_id_str", req.workflow_instance_id)
    if ext:
        result["_ext_info"] = ext

    data = TaskCallbackData(
        loop_task_id=loop_task_id,
        workflow_type=workflow_type,
        workflow_id=wf_id_int,
        instance_id=inst_id_int,
        result=result,
    )
    return TranslatedCallback(disposition=disposition, data=data)