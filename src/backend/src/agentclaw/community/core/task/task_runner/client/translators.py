"""三翻译器:执行主体终态 dict → TaskCallbackData(loop_task_id/result)。

零 case:仅消费 dict 字段,不出现节点名。结果回流经 ResultSink → on_report。
关键区分(Step2 harness):run **FAILED/SLA/poll 耗尽 = 执行报错**(bot 没跑通)→ result["exec_error"];
run **COMPLETED = 执行通** → 严格解析 content 中的结构化验收判定 {success,data,gaps};非法终态进 Harness。
执行报错≠验收不过:前者→harness 重投,后者→补救重规划。
"""
from __future__ import annotations

from typing import Any

from agentclaw.community.core.task.domain.json_extract import extract_json
from agentclaw.community.core.task.domain.models import TaskCallbackData


def _cb(loop_task_id: str, workflow_type: str, *, success: bool, data: Any = None,
        gaps: list[str] | None = None, fail_detail: str | None = None,
        exec_error: str | None = None) -> TaskCallbackData:
    """组装 TaskCallbackData。exec_error(执行报错)与 success+gaps(验收)互斥。

    ``fail_detail`` 只保留旧调用方过渡兼容；新终态使用 ``gaps``。
    """
    result: dict[str, Any] = {"success": success}
    if data is not None:
        result["data"] = data
    if fail_detail is not None:
        result["fail_detail"] = fail_detail
    if gaps is not None:
        result["gaps"] = gaps
    if exec_error is not None:
        result["exec_error"] = exec_error
    return TaskCallbackData(data={
        "loop_task_id": loop_task_id,
        "workflow_type": workflow_type,
        "workflow_id": 0,
        "instance_id": 0,
        "result": result,
    })


def _parse_acceptance(content: Any) -> tuple[bool, list[str], Any]:
    """严格解析 ``{success: bool, data, gaps: list[str]}``;旧 fail_detail 归一成单 gap。"""
    if not content:
        raise ValueError("empty terminal content")
    try:
        obj = extract_json(content) if isinstance(content, str) else content
    except (ValueError, TypeError) as exc:
        raise ValueError("terminal content is not valid JSON") from exc
    if not isinstance(obj, dict):
        raise ValueError("terminal result must be a JSON object")
    success = obj.get("success")
    if type(success) is not bool:
        raise ValueError("success must be bool")
    raw_gaps = obj.get("gaps")
    if raw_gaps is None and isinstance(obj.get("fail_detail"), str):
        raw_gaps = [obj["fail_detail"]]
    if raw_gaps is None:
        gaps: list[str] = []
    elif isinstance(raw_gaps, list):
        if any(not isinstance(gap, str) for gap in raw_gaps):
            raise ValueError("gaps must be a list of strings")
        gaps = [gap.strip() for gap in raw_gaps if gap.strip()]
    else:
        raise ValueError("gaps must be a list of strings")
    if not success and not gaps:
        raise ValueError("failed acceptance must include non-empty gaps")
    return success, gaps, obj.get("data")


def _completed(loop_task_id: str, workflow_type: str, content: Any) -> TaskCallbackData:
    try:
        success, gaps, data = _parse_acceptance(content)
    except ValueError as exc:
        return _cb(
            loop_task_id, workflow_type, success=False,
            exec_error=f"terminal_result_invalid: {exc}",
        )
    return _cb(loop_task_id, workflow_type, success=success, data=data, gaps=gaps)


class SingleBotRunTranslator:
    @staticmethod
    def adapt(run_dict: dict[str, Any], loop_task_id: str) -> TaskCallbackData:
        status = str(run_dict.get("status") or "").lower()
        err = run_dict.get("error")
        if status != "completed":
            # 执行报错(run FAILED):非验收 → exec_error,走 harness
            fail_reason = "timeout" if (err and str(err).upper() == "TIME_OUT") else (err or f"run_{status}")
            return _cb(loop_task_id, "single_bot", success=False, exec_error=str(fail_reason))
        # run COMPLETED = 执行通 → 严格解析结构化验收判定；非法终态走 harness
        data = (run_dict.get("result") or {}).get("content")
        return _completed(loop_task_id, "single_bot", data)


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
        return _completed(loop_task_id, "bcn_coop_group", data)


class BcsStateMachineRunTranslator:
    @staticmethod
    def adapt(run_dict: dict[str, Any], loop_task_id: str) -> TaskCallbackData:
        status = str(run_dict.get("status") or "").lower()
        if status != "completed":
            err = run_dict.get("error")
            return _cb(loop_task_id, "bcn_coop_group", success=False,
                       exec_error="aborted" if status == "aborted" else (err or f"run_{status}"))
        data = run_dict.get("output")
        return _completed(loop_task_id, "bcn_coop_group", data)
