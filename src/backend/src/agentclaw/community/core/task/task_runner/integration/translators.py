"""三翻译器:执行主体终态 dict → TaskCallbackData(loop_task_id/result{success,data,fail_detail})。

零 case:仅消费 dict 字段,不出现节点名。结果回流经 ResultSink → on_report。
"""
from __future__ import annotations

from typing import Any

from agentclaw.community.core.task.domain.models import TaskCallbackData


def _cb(loop_task_id: str, workflow_type: str, *, success: bool, data: Any = None,
        fail_detail: str | None = None) -> TaskCallbackData:
    result: dict[str, Any] = {"success": success}
    if data is not None:
        result["data"] = data
    if fail_detail is not None:
        result["fail_detail"] = fail_detail
    return TaskCallbackData(
        loop_task_id=loop_task_id, workflow_type=workflow_type,
        workflow_id=0, instance_id=0, result=result,
    )


class SingleBotRunTranslator:
    @staticmethod
    def adapt(run_dict: dict[str, Any], loop_task_id: str) -> TaskCallbackData:
        status = str(run_dict.get("status") or "").lower()
        success = status == "completed"
        data = (run_dict.get("result") or {}).get("content")
        err = run_dict.get("error")
        fail_detail = "timeout" if (err and str(err).upper() == "TIME_OUT") else err
        return _cb(loop_task_id, "single_bot", success=success, data=data, fail_detail=fail_detail)


class BcsSessionTranslator:
    @staticmethod
    def adapt(group_dict: dict[str, Any], messages: list[Any], loop_task_id: str) -> TaskCallbackData:
        sess = group_dict.get("session") or {}
        status = str(sess.get("status") or "").lower()
        success = status == "completed"
        data = sess.get("output")
        if data is None:
            for m in reversed(messages):
                if (m.get("role") if isinstance(m, dict) else None) == "assistant":
                    data = m.get("content")
                    break
        fail_detail = sess.get("error_message")
        return _cb(loop_task_id, "bcn_coop_group", success=success, data=data, fail_detail=fail_detail)


class BcsStateMachineRunTranslator:
    @staticmethod
    def adapt(run_dict: dict[str, Any], loop_task_id: str) -> TaskCallbackData:
        status = str(run_dict.get("status") or "").lower()
        success = status == "completed"
        data = run_dict.get("output")
        err = run_dict.get("error")
        fail_detail = "aborted" if status == "aborted" else err
        return _cb(loop_task_id, "bcn_coop_group", success=success, data=data, fail_detail=fail_detail)
