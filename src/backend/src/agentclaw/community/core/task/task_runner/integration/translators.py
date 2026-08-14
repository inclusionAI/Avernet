"""三翻译器:执行主体终态 dict → TaskCallbackData(loop_task_id/result)。

零 case:仅消费 dict 字段,不出现节点名。结果回流经 ResultSink → on_report。
关键区分(Step2 harness):run **FAILED/SLA/poll 耗尽 = 执行报错**(bot 没跑通)→ result["exec_error"];
run **COMPLETED = 执行通** → 解析 content 中的结构化验收判定 {success,fail_detail,data}(缺省 PASS)。
执行报错≠验收不过:前者→harness 重投,后者→补救重规划。
"""
from __future__ import annotations

from typing import Any

from agentclaw.community.core.task.domain.json_extract import extract_json
from agentclaw.community.core.task.domain.models import TaskCallbackData


def _cb(loop_task_id: str, workflow_type: str, *, success: bool, data: Any = None,
        fail_detail: str | None = None, exec_error: str | None = None) -> TaskCallbackData:
    """组装 TaskCallbackData。exec_error(执行报错)与 success+fail_detail(验收)互斥:
    exec_error 非空 → 纯执行报错(无验收);否则 success/fail_detail 表验收判定。"""
    result: dict[str, Any] = {"success": success}
    if data is not None:
        result["data"] = data
    if fail_detail is not None:
        result["fail_detail"] = fail_detail
    if exec_error is not None:
        result["exec_error"] = exec_error
    return TaskCallbackData(
        loop_task_id=loop_task_id, workflow_type=workflow_type,
        workflow_id=0, instance_id=0, result=result,
    )


def _parse_acceptance(content: Any) -> tuple[bool, str | None, Any] | None:
    """尝试把 run COMPLETED 的 content 解析为结构化验收判定 {success, fail_detail, data}。
    成功解析(含 success 字段)→ (success, fail_detail, data);无法解析 → None(调用方走缺省 PASS)。
    支持:裸 JSON / ```json 代码块 / 散文包裹(经 ``extract_json``)。"""
    if not content:
        return None
    try:
        obj = extract_json(content) if isinstance(content, str) else content
    except (ValueError, TypeError):
        return None
    if isinstance(obj, dict) and "success" in obj:
        return bool(obj.get("success")), obj.get("fail_detail"), obj.get("data")
    return None


class SingleBotRunTranslator:
    @staticmethod
    def adapt(run_dict: dict[str, Any], loop_task_id: str) -> TaskCallbackData:
        status = str(run_dict.get("status") or "").lower()
        err = run_dict.get("error")
        if status != "completed":
            # 执行报错(run FAILED):非验收 → exec_error,走 harness
            fail_reason = "timeout" if (err and str(err).upper() == "TIME_OUT") else (err or f"run_{status}")
            return _cb(loop_task_id, "single_bot", success=False, exec_error=str(fail_reason))
        # run COMPLETED = 执行通 → 解析结构化验收判定(缺省 PASS)
        data = (run_dict.get("result") or {}).get("content")
        parsed = _parse_acceptance(data)
        if parsed is not None:
            ok, fail_detail, acc_data = parsed
            return _cb(loop_task_id, "single_bot", success=ok, data=acc_data if acc_data is not None else data, fail_detail=fail_detail)
        return _cb(loop_task_id, "single_bot", success=True, data=data)


class BcsSessionTranslator:
    @staticmethod
    def adapt(group_dict: dict[str, Any], messages: list[Any], loop_task_id: str) -> TaskCallbackData:
        sess = group_dict.get("session") or {}
        status = str(sess.get("status") or "").lower()
        if status == "failed" or status == "aborted":
            return _cb(loop_task_id, "bcn_coop_group", success=False,
                       exec_error=sess.get("error_message") or f"session_{status}")
        data = sess.get("output")
        if data is None:
            for m in reversed(messages):
                if (m.get("role") if isinstance(m, dict) else None) == "assistant":
                    data = m.get("content")
                    break
        parsed = _parse_acceptance(data)
        if parsed is not None:
            ok, fail_detail, acc_data = parsed
            return _cb(loop_task_id, "bcn_coop_group", success=ok,
                       data=acc_data if acc_data is not None else data, fail_detail=fail_detail)
        return _cb(loop_task_id, "bcn_coop_group", success=True, data=data)


class BcsStateMachineRunTranslator:
    @staticmethod
    def adapt(run_dict: dict[str, Any], loop_task_id: str) -> TaskCallbackData:
        status = str(run_dict.get("status") or "").lower()
        if status != "completed":
            err = run_dict.get("error")
            return _cb(loop_task_id, "bcn_coop_group", success=False,
                       exec_error="aborted" if status == "aborted" else (err or f"run_{status}"))
        data = run_dict.get("output")
        parsed = _parse_acceptance(data)
        if parsed is not None:
            ok, fail_detail, acc_data = parsed
            return _cb(loop_task_id, "bcn_coop_group", success=ok,
                       data=acc_data if acc_data is not None else data, fail_detail=fail_detail)
        return _cb(loop_task_id, "bcn_coop_group", success=True, data=data)
