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
from agentclaw.community.core.task.task_runner.client.callback_data_enricher import (
    _bcn_state_machine_status,
    _claw_mind_status_to_task,
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
    - ``loop_task_id`` = ``flow_id``(run 实例 id,对齐 BCN 的 ``scope.run_id`` 回投键 → ``task_callback.run_id`` 列存 run 实例);工作流级回投,``node_id`` 空;
    - ``workflow_instance_id`` = ``ext_info.flow_runs.origin_session_id``(session_id → 落 task_callback.main_session_id);
    - ``status`` = 底层 status(``ext_info.flow_runs.status`` > ``node_executions[0].status`` > 顶层)经
      ``_claw_mind_status_to_task`` 映射的 task Status 枚举值(落 ``task_callback.status``,ClawMind 7 态 → task 7 态语义对应);
    - ``result.success`` 由底层 status 语义推(succeeded/completed→True;failed/cancelled/aborted→False;未知→不设 success);
    - ``result.data`` = ``node_executions[0].output_json``;``result.exec_error`` = ``node_executions[0].error_text``;
    - ``execution_graph`` 不在此构建(ClawMind 图由 ``CallbackDataEnricher.enrich_claw_mind`` 从 ``ext_info`` 纯函数构建,adapter 仅做字段映射);
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
    # execution_graph 由 CallbackDataEnricher.enrich_claw_mind 从 ext_info 构建(adapter 仅做字段映射);
    # 原始 body 留作 _raw_callback_body → task_callback.orig_callback_data。

    return TranslatedCallback(
        disposition=disposition,
        data=TaskCallbackData(data={
            "loop_task_id": (raw.get("flow_id") or ""),
            "workflow_type": "single_bot",
            "workflow_id": 0,
            "instance_id": 0,
            "workflow_source": "claw_mind",
            "workflow_instance_id": (flow_runs.get("origin_session_key") or flow_runs.get("origin_session_id") or ""),
            "status": _claw_mind_status_to_task(low_status).value,
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

def is_common_task_payload(raw: Any) -> bool:
    return (isinstance(raw, dict)
            and "task_id" in raw
            and "node_id" in raw
            and "status" in raw
            and "output" in raw)

def translate_bcn(raw: dict) -> TranslatedCallback | None:
    """BCN CloudEvent → TaskCallbackData.data dict(对齐 task_callback 列);非处理事件返 ``None``。

    仅处理 ``_BCN_HANDLED_EVENTS``;disposition 由 event_type 推(created/started→start,completed→result)。
    字段映射:
    - ``invoker`` = "bcn";``loop_task_id`` = ``scope.run_id``(node_id 恒空,req4);
    - ``workflow_instance_id`` = ``scope.session_id``(→ main_session_id);
    - ``status`` = ``_bcn_state_machine_status(event_type)``(``run.completed``→``DONE``,其余→``RUNNING``,req2);
    - ``execution_graph`` 不在此构建(由 ``CallbackDataEnricher.enrich_bcn`` 取 BCS run 明细/DAG 后构建;fetch 失败用事件体兜底);
    - ``result.success`` 由 event_type 推(completed→True,除非 ``data.outcome`` 为 failed/error);``result.data`` = ``data.output``;
    - ``result.exec_error`` = ``data.reason``/``data.error_text``(若有);
    - ``event_id`` = ``raw.event_id``(CloudEvent 幂等键;避免 run 明细经 ``result._ext_info`` 落 ``extend_props`` 后摘要素乱);
    - ``_raw_callback_body`` = 原始 event(→ orig_callback_data,router 不再覆盖);``extend_props`` 不设
      (router 取回 run 明细后落 ``result._ext_info`` → ``extend_props``,req3)。
    """
    event_type = raw.get("event_type")
    if event_type not in _BCN_HANDLED_EVENTS:
        return None
    scope = raw.get("scope")
    scope = scope if isinstance(scope, dict) else {}
    data = raw.get("data")
    data = data if isinstance(data, dict) else {}

    run_id = scope.get("run_id") or ""
    # req4:node_id 恒空,loop_task_id = run_id(回调不走框架节点名;run_id 即回投键)。
    loop_task_id = run_id

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
            "event_id": raw.get("event_id"),
            "status": _bcn_state_machine_status(event_type).value,
            "_raw_callback_body": raw,
            "result": result,
        }),
    )

def translate_common_task_callback(raw: dict) -> TranslatedCallback | None:
    disposition: Literal["start", "result"] = "result"
    return TranslatedCallback(
        disposition=disposition,
        data=TaskCallbackData(data={
            "loop_task_id": raw.get("task_id"),
            "node_id": raw.get("node_id"),
            "workflow_type": "task_loop",
            "workflow_id": 0,
            "instance_id": 0,
            "workflow_source": "task_loop",
            "workflow_instance_id": "",
            "event_id": "",
            "status": raw.get("status"),
            "_raw_callback_body": raw,
            "result": raw.get("output")
        }),
    )


# ===== BCN manager_worker(任务协作群)事件解析 + execution_graph 累积 merge(语雀 §4) =====
# manager_worker 群无 state_machine run;子任务由 Manager 分配给 Worker,各自持有 task_id 与独立 stream。
# 跨 stream 无全局顺序(group.created/session.created/task.*/session.completed 分属不同 stream),
# 接入方用 scope.{group_id,session_id,task_id} 关联整条链、容忍乱序;同一事件可能重投(event_id 去重)。
# 框架按 (run_id=session_id, node_id="") 单 session 行 upsert,把事件 merge 进 execution_graph。

_BCN_MANAGER_WORKER_EVENTS = frozenset({
    "group.created",
    "session.created",
    "task.assigned",
    "task.completed",
    "session.completed",
})


def parse_manager_worker_bcn(raw) -> dict | None:
    """manager_worker CloudEvent → 落库/merge 所需字段 dict;非 manager_worker 事件返 ``None``。

    ``event_id``/``event_type`` 取自带元,``group_id``/``session_id``/``task_id`` 取自 scope,``data``
    取原始事件体(供 merge 按 event_type 取字段)。session_id 可空(如 group.created 前的 session.created
    理论上带 session_id;极端缺失时留空字符串不阻断)。"""
    if not isinstance(raw, dict):
        return None
    event_type = raw.get("event_type")
    if event_type not in _BCN_MANAGER_WORKER_EVENTS:
        return None
    scope = raw.get("scope") if isinstance(raw.get("scope"), dict) else {}
    data = raw.get("data") if isinstance(raw.get("data"), dict) else {}
    return {
        "event_id": raw.get("event_id"),
        "event_type": event_type,
        "group_id": scope.get("group_id") or "",
        "session_id": scope.get("session_id") or "",
        "task_id": scope.get("task_id") or "",
        "data": data,
    }


# 子任务 status 单调秩:防止乱序投递时后到的非终态(assigned)回退已写的终态(completed)。
_TASK_ENTRY_STATUS_RANK = {"assigned": 1, "completed": 2}


def _upsert_task_entry(tasks: list, entry: dict) -> None:
    """按 ``task_id`` upsert 子任务条目(后到自己覆盖;``None`` 值不覆盖以保留前一个事件已写的信息)。

    ``status`` 单调保护:已 ``completed`` 的子任务不被后到的 ``assigned`` 回退(乱序兜底);
    有序投递(assigned→completed)秩递增,照常覆盖。其余字段仍"非 None 即覆盖"。"""
    tid = entry.get("task_id")
    idx = next((i for i, t in enumerate(tasks) if t.get("task_id") == tid), None)
    if idx is not None:
        merged = dict(tasks[idx])
        for k, v in entry.items():
            if v is None:
                continue
            if (k == "status"
                    and _TASK_ENTRY_STATUS_RANK.get(merged.get("status"), 0)
                    > _TASK_ENTRY_STATUS_RANK.get(v, 0)):
                continue  # 已处更高状态,不回退(completed 不被 assigned 覆盖)
            merged[k] = v
        tasks[idx] = merged
    else:
        tasks.append({k: v for k, v in entry.items() if v is not None})


def merge_manager_worker_execution_graph(
    existing: dict | None, parsed: dict,
) -> dict:
    """把单次 manager_worker 事件 merge 进(按 session_id 累积的)任务状态图谱。

    累积结构:``{session_id, group_id, group_status, session_status,
    tasks:[{task_id, manager_id, worker_id, status, assignment?, result?, completed_at?}],
    session_completed_by, session_summary, last_event_type}``。``existing`` 为 ``None`` → 初始化新图谱。
    ``task.assigned``/``task.completed`` 按 ``task_id`` upsert(乱序容忍、后到自己)。"""
    state: dict[str, Any] = dict(existing) if existing else {}
    state.setdefault("tasks", [])
    state.setdefault("group_status", None)
    state.setdefault("session_status", None)
    et = parsed.get("event_type")
    data = parsed.get("data") or {}
    sid = parsed.get("session_id") or ""
    gid = parsed.get("group_id") or ""
    if sid:
        state["session_id"] = sid
    if gid:
        state["group_id"] = gid
    state["last_event_type"] = et
    if et == "group.created":
        state["group_status"] = data.get("status")
    elif et == "session.created":
        state["session_status"] = data.get("status") or "active"
    elif et == "task.assigned":
        tid = parsed.get("task_id") or data.get("task_id")
        _upsert_task_entry(state["tasks"], {
            "task_id": tid, "manager_id": data.get("manager_id"),
            "worker_id": data.get("worker_id"), "status": "assigned",
            "assignment": data.get("assignment"), "session_id": sid,
        })
    elif et == "task.completed":
        tid = parsed.get("task_id") or data.get("task_id")
        _upsert_task_entry(state["tasks"], {
            "task_id": tid, "manager_id": data.get("manager_id"),
            "worker_id": data.get("worker_id"), "status": "completed",
            "result": data.get("result"), "completed_at": data.get("completed_at"),
            "session_id": sid,
        })
    elif et == "session.completed":
        state["session_status"] = data.get("reason") or "completed"
        state["session_completed_by"] = data.get("completed_by")
        state["session_summary"] = data.get("summary")
    return state