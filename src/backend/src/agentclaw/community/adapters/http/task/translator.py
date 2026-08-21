"""回调请求边缘翻译:羽雀 schema → SSOT TaskCallbackData(+disposition)。

SSOT TaskCallbackData 不扩;ext_info/goal/未登记 str id 塞进 result["_ext_info"],
由 CallbackAdapter.adapt/adapt_start 折进 extend_props_patch。零 case:仅消费 schema 字段
+ loop_task_id/node_id/workflow_source,无节点名字面量。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from agentclaw.community.core.errors import NotFound
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
                raise NotFound(
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

    return TranslatedCallback(
        disposition=disposition,
        data=TaskCallbackData(data={
            "loop_task_id": loop_task_id,
            "workflow_type": workflow_type,
            "workflow_id": wf_id_int,
            "instance_id": inst_id_int,
            "workflow_source": source,
            "workflow_instance_id": req.workflow_instance_id,
            "result": result,
        }),
    )


# ===== ClawMind HttpCallbackPayload 解析(语雀《ClawMind回调服务》§八) =====
# 回调调用者仅 ClawMind / BCN。ClawMind 回调 body 为固定形状 HttpCallbackPayload:
#   { "workflow_id", "flow_id", "status", "ext_info": { "flow_runs": {...}, "node_executions": [{...}] } }
# 顶层 ``status`` 取值不统一(node_*/started/succeeded/failed/cancelled),故底层 status 优先。

_CLAW_MIND_PAYLOAD_FIELDS = ("workflow_id", "flow_id", "status", "ext_info")

# 底层 status → success 语义映射(succeeded/completed→True;failed/cancelled/aborted→False;未知→缺省)。
_CLAW_MIND_SUCCESS: dict[str, bool] = {
    "succeeded": True, "completed": True, "node_succeeded": True, "success": True,
    "failed": False, "cancelled": False, "canceled": False, "aborted": False, "node_failed": False,
}


def is_claw_mind_payload(raw: Any) -> bool:
    """ClawMind 回调识别:body 解析为 dict 且含 HttpCallbackPayload 四个顶层字段。"""
    return isinstance(raw, dict) and all(k in raw for k in _CLAW_MIND_PAYLOAD_FIELDS)


def translate_claw_mind(raw: dict, disposition: Literal["start", "result"]) -> TranslatedCallback:
    """ClawMind HttpCallbackPayload → TaskCallbackData.data dict(对齐语雀 §八)。

    字段映射:
    - ``loop_task_id`` = ``workflow_id``(ClawMind 每次回投上报整 workflow → workflow 级回投,``node_id`` 空);
    - ``workflow_instance_id`` = ``ext_info.flow_runs.origin_session_id``(session_id → 落 task_callback.main_session_id);
    - ``status`` 从底层 status 推:``ext_info.flow_runs.status`` > ``node_executions[0].status``(顶层 status 仅粗粒度事件类型);
    - ``result.success`` 由底层 status 语义推(succeeded/completed→True;failed/cancelled/aborted→False;未知→不设 success);
    - ``result.data`` = ``node_executions[0].output_json``;``result.exec_error`` = ``node_executions[0].error_text``;
    - ``execution_graph`` = 全量 ``ext_info``(flow_runs + node_executions 快照 → task_callback.execution_graph);
    - ``_raw_callback_body`` = 原始 body(→ task_callback.orig_callback_data);
    - ``extend_props`` 不设(claw_mind 无额外扩展,graph 已在 execution_graph);其余字段按语义,无则 ``""`` / ``0``。
    """
    ext = raw.get("ext_info")
    ext = ext if isinstance(ext, dict) else {}
    flow_runs = ext.get("flow_runs")
    flow_runs = flow_runs if isinstance(flow_runs, dict) else {}
    node_execs = ext.get("node_executions")
    node_execs = node_execs if isinstance(node_execs, list) else []
    first_node = node_execs[0] if (node_execs and isinstance(node_execs[0], dict)) else {}

    low_status = (flow_runs.get("status") or first_node.get("status") or raw.get("status") or "")
    success = _CLAW_MIND_SUCCESS.get(str(low_status).lower())

    result: dict[str, Any] = {}
    if success is not None:
        result["success"] = success
    out = first_node.get("output_json")
    if out is not None:
        result["data"] = out
    err = first_node.get("error_text")
    if err:
        result["exec_error"] = err
    # ext_info 全量作 execution_graph 快照(不再折进 result._ext_info,避免与 execution_graph 重复);
    # 原始 body 留作 _raw_callback_body → task_callback.orig_callback_data。

    return TranslatedCallback(
        disposition=disposition,
        data=TaskCallbackData(data={
            "loop_task_id": (raw.get("workflow_id") or ""),
            "workflow_type": "single_bot",
            "workflow_id": 0,
            "instance_id": 0,
            "workflow_source": "claw_mind",
            "workflow_instance_id": (flow_runs.get("origin_session_id") or ""),
            "status": low_status,
            "execution_graph": ext,
            "_raw_callback_body": raw,
            "result": result,
        }),
    )


# ===== BCN(BCS Group)CloudEvent 回调解析(语雀《BCS Group 回调接入说明》) =====
# 回调为 CloudEvent 信封:{event_id, event_type, source="bcs", scope{group_id,session_id,run_id},
# stream, actor, data{...随 event_type 变}}。仅处理以下 5 个 state_machine 事件,其余返回 None(不处理)。

_BCN_HANDLED_EVENTS = frozenset({
    "state_machine.run.created",
    "state_machine.run.started",
    "state_machine.node.started",
    "state_machine.node.completed",
    "state_machine.run.completed",
})


def is_bcn_event_payload(raw: Any) -> bool:
    """BCN 回调识别:CloudEvent 信封,含 ``event_id`` + ``event_type``(str) + ``scope``(dict)。"""
    return (isinstance(raw, dict)
            and isinstance(raw.get("event_type"), str)
            and isinstance(raw.get("scope"), dict)
            and "event_id" in raw)


def translate_bcn(raw: dict) -> TranslatedCallback | None:
    """BCN CloudEvent → TaskCallbackData.data dict(对齐 task_callback 列);非处理事件返 ``None``。

    仅处理 ``_BCN_HANDLED_EVENTS``;disposition 由 event_type 推(created/started→start,completed→result)。
    字段映射(拟,可调):
    - ``invoker`` = "bcn";``loop_task_id`` = ``scope.run_id``(+ ``::data.node_id`` 若有);
    - ``workflow_instance_id`` = ``scope.session_id``(→ main_session_id);
    - ``status`` = ``event_type``;``execution_graph`` = ``data``(事件体);
    - ``result.success`` 由 event_type 推(completed→True,除非 ``data.outcome`` 为 failed/error);``result.data`` = ``data.output``;
    - ``result.exec_error`` = ``data.reason``/``data.error_text``(若有);
    - ``_raw_callback_body`` = 原始 event(→ orig_callback_data);``extend_props`` 不设。
    """
    event_type = raw.get("event_type")
    if event_type not in _BCN_HANDLED_EVENTS:
        return None
    scope = raw.get("scope")
    scope = scope if isinstance(scope, dict) else {}
    data = raw.get("data")
    data = data if isinstance(data, dict) else {}

    run_id = scope.get("run_id") or ""
    node_id = data.get("node_id") or ""
    loop_task_id = f"{run_id}::{node_id}" if node_id else run_id

    is_completed = event_type.endswith(".completed")
    result: dict[str, Any] = {}
    if is_completed:
        outcome = str(data.get("outcome") or "").lower()
        result["success"] = outcome not in ("failed", "error")
        out = data.get("output")
        if out is not None:
            result["data"] = out
    reason = data.get("reason") or data.get("error_text")
    if reason:
        result["exec_error"] = reason

    disposition: Literal["start", "result"] = "result" if is_completed else "start"
    return TranslatedCallback(
        disposition=disposition,
        data=TaskCallbackData(data={
            "loop_task_id": loop_task_id,
            "workflow_type": "bcn_coop_group",
            "workflow_id": 0,
            "instance_id": 0,
            "workflow_source": "bcn",
            "workflow_instance_id": (scope.get("session_id") or ""),
            "status": event_type,
            "execution_graph": data,
            "_raw_callback_body": raw,
            "result": result,
        }),
    )