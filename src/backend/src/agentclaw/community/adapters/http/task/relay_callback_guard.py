"""Relay callback protocol guards and callback failure trajectory recording."""

from __future__ import annotations

from typing import Any

from agentclaw.community.api.task.task_service import TaskServiceProtocol
from agentclaw.community.adapters.http.task.translator import is_common_task_payload
from agentclaw.community.core.task.domain.errors import TaskStateError
from agentclaw.community.log import get_logger

logger = get_logger()


def record_relay_callback_error(
    svc: TaskServiceProtocol, raw_obj: dict[str, Any], phase: str, exc: Exception,
) -> None:
    def value(key: str) -> str:
        return str(raw_obj.get(key) or "")

    payload = raw_obj.get("payload")
    try:
        svc.record_relay_callback_error(
            task_id=value("task_id"), node_id=value("node_id"),
            event_type=value("event_type"), event_id=value("event_id"),
            holder_id=value("holder_id"), relay_turn=value("relay_turn") or None,
            progress_reason=value("progress_reason") or None,
            failure_reason=value("failure_reason") or None,
            payload=payload if isinstance(payload, dict) else None, error_phase=phase,
            exception_type=type(exc).__name__, error_msg=str(exc),
        )
    except Exception as emit_exc:  # noqa: BLE001 trajectory must not mask callback
        logger.warning(
            "[task][relay][callback] failure trajectory failed phase=%s: %s",
            phase, emit_exc, exc_info=True,
        )


def _legacy_result_task_id(raw_obj: Any) -> str | None:
    """Extract the routed task from a non-event task callback payload."""
    if not isinstance(raw_obj, dict) or raw_obj.get("event_type") is not None:
        return None
    if is_common_task_payload(raw_obj):
        return str(raw_obj.get("task_id") or "") or None
    loop_task_id = raw_obj.get("loop_task_id")
    if isinstance(loop_task_id, str) and "::" in loop_task_id:
        return loop_task_id.split("::", 1)[0] or None
    # Rich TaskCallbackRequest also carries task_id + status. It is a legacy
    # task-level terminal result and must not bypass Relay events.
    if raw_obj.get("task_id") and raw_obj.get("status") is not None:
        return str(raw_obj["task_id"])
    return None


def reject_legacy_relay_result(
    disposition: str, raw_obj: Any, svc: TaskServiceProtocol,
) -> None:
    """Reject Relay result calls that bypass the required event protocol."""
    if disposition != "result":
        return
    task_id = _legacy_result_task_id(raw_obj)
    if task_id is None:
        return
    graph = svc.get_task_dashboard(task_id)
    config = graph.extend_props.get("execution_config", {}) or {}
    if config.get("orchestration_mode") == "relay":
        raise TaskStateError(
            "relay 任务结果回投必须使用 EXECUTION_RESULT/PLAN_RESULT/DISPATCH_RESULT 事件"
        )
