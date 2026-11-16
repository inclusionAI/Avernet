"""Trusted Cron response projections; never infer a runtime from request fields."""

from datetime import datetime, timezone as _tz
from typing import Any
from .converter_run_content import run_content

from .schemas import (
    Routine, RoutineRun, ScheduleTrigger, _coerce_timeout_secs,
    RoutineFailedTarget, RoutineOperationBatch, RoutineOperationResult,
)

_TARGET_FIELDS = ("bot_id", "owner_id", "runtime_stage", "device_uuid", "operator_id")
_FAILURE_REASONS = {
    "cron_api_failed", "cron_api_timeout", "instances_query_failed", "instances_query_timeout",
    "instances_not_found", "resolver_failed", "binding_not_active", "device_unavailable",
    "runtime_unavailable", "binding_unavailable", "caller_instance_not_ready",
    "caller_unavailable", "binding_missing", "publish_query_failed",
}


def target_context(data: dict) -> dict:
    context = {**(data.get("bot") or {}), **data}
    return {key: context[key] for key in _TARGET_FIELDS if context.get(key) is not None}


def failed_targets(result: dict) -> list[RoutineFailedTarget]:
    return [RoutineFailedTarget(**target_context(row), reason=safe_reason(row.get("reason")))
            for row in result.get("failed_targets", []) if isinstance(row, dict)]


def safe_reason(reason) -> str:
    return reason if reason in _FAILURE_REASONS else "cron_api_failed"


def _ms_to_iso(ms: Any) -> str:
    """Convert epoch milliseconds to an ISO 8601 string; ``""`` on falsy/invalid."""
    if not ms:
        return ""
    try:
        return datetime.fromtimestamp(int(ms) / 1000, tz=_tz.utc).isoformat()
    except (TypeError, ValueError, OverflowError, OSError):
        return ""


def _map_routine(data: dict) -> Routine:
    """Map an engine adapter cron dict → openapi Routine schema.

    Field source: ``plugins/local/device_adapter_transport.py`` `_build_item`
    (``id`` / ``name`` / ``enabled`` / ``schedule{expr,tz}`` /
    ``payload.message`` / ``payload.timeout_secs`` / ``payload.model`` /
    ``created_at_ms`` / ``updated_at_ms``) plus the top-level ``bot_id``
    injected by ``_decorate_runtime_item`` in ``cron_runtime_targets.py``.
    The environment settings live in the payload — nothing in the produce
    chain ever reports them at the item top level.
    """
    data = {**(data.get("bot") or {}), **data}
    sched = data.get("schedule") or {}
    payload = data.get("payload") or {}
    state = data.get("state")
    next_run_ms = state.get("next_run_at_ms") if isinstance(state, dict) else None
    model = payload.get("model")
    timeout_secs = _coerce_timeout_secs(payload.get("timeout_secs"))
    return Routine(
        routine_id=str(data.get("id") or data.get("task_id") or ""),
        bot_id=str(data.get("bot_id", "")),
        bot_name=data.get("bot_name") or None,
        owner_id=data.get("owner_id") or None,
        runtime_stage=data.get("runtime_stage") or None,
        device_uuid=data.get("device_uuid"),
        operator_id=data.get("operator_id"),
        name=str(data.get("name", "") or ""),
        trigger=ScheduleTrigger(type="schedule", cron=str(sched.get("expr", ""))),
        command=str(payload.get("message", "") or ""),
        enabled=bool(data.get("enabled", False)),
        timezone=sched.get("tz") or None,
        model=str(model) if model else None,
        timeout_secs=timeout_secs,
        next_run_at=_ms_to_iso(next_run_ms) or None,
        gmt_create=_ms_to_iso(data.get("created_at_ms")),
        gmt_modified=_ms_to_iso(data.get("updated_at_ms")),
    )


def _map_run(data: dict, routine_id: str, context: dict | None = None) -> RoutineRun:
    """Map an engine adapter run dict → openapi RoutineRun.

    Field source: adapter ``runs[]`` entries from
    ``plugins/local/device_adapter_transport.py`` carry ``job_id`` +
    ``started_at_ms`` / ``finished_at_ms`` / ``status`` / ``error`` /
    ``duration_ms``. ``RoutineRun`` has no duration field, so we carry
    status + timestamps and synthesize ``run_id`` from ``job_id`` (test
    stubs sometimes use ``run_id`` — accept both). ``_ms_to_iso`` returns
    ``""`` on falsy; coerce to ``None`` to match the ``str | None`` schema.
    """
    job_id = data.get("job_id") or data.get("run_id") or data.get("id") or ""
    started = _ms_to_iso(data.get("started_at_ms")) or None
    finished = _ms_to_iso(data.get("finished_at_ms")) or None
    return RoutineRun(
        **target_context({**data, **(context or {})}),
        run_id=str(job_id),
        routine_id=routine_id,
        status=str(data.get("status", "") or ""),
        started_at=started,
        finished_at=finished,
        **run_content(data),
    )



def map_trigger(data: dict, routine_id: str, context: dict | None = None) -> RoutineRun:
    runtime_status = str(data.get("status", "") or "").lower()
    reason = bool(data.get("reason"))
    ran = data.get("ran")
    if runtime_status in {"dispatched", "started", "running", "success", "completed"}:
        status = "completed"
    elif runtime_status in {"already_running", "not_due", "skipped", "failed", "error"}:
        status = "failed"
    elif isinstance(ran, bool):
        status = "completed" if ran else ("failed" if reason else "unknown")
    elif ran:
        status = "completed"
    else:
        status = "failed" if reason else "unknown"
    upstream_run_id = data.get("run_id") or data.get("runId")
    return RoutineRun(
        **target_context({**data, **(context or {})}),
        run_id=str(upstream_run_id) if upstream_run_id else f"{routine_id}-{datetime.now(_tz.utc).isoformat()}",
        routine_id=routine_id, status=status,
    )


def map_operation_batch(result: dict, routine_id: str, *, run: bool) -> RoutineOperationBatch:
    data = result["data"]
    rows = []
    for row in data["results"]:
        context = target_context(row)
        success = bool(row.get("success"))
        body = row.get("data")
        routine = trigger = None
        if success and isinstance(body, dict):
            if run:
                trigger = map_trigger(body, routine_id, context)
            else:
                routine = _map_routine({**body, **context})
        rows.append(RoutineOperationResult(
            **context, success=success, routine=routine, run=trigger,
            reason=None if success else safe_reason(row.get("reason")),
        ))
    return RoutineOperationBatch(results=rows, succeeded=data["succeeded"],
                                 failed=data["failed"], failed_targets=failed_targets(result))


def map_runs(result: dict, routine_id: str) -> list[RoutineRun]:
    data = result.get("data")
    if isinstance(data, dict) and isinstance(data.get("results"), list):
        mapped = [
            _map_run({"input": (row.get("data") or {}).get("input"), **item}, routine_id, target_context(row))
            for row in data["results"] if row.get("success")
            for item in (row.get("data") or {}).get("runs", []) if isinstance(item, dict)
        ]
    else:
        raw = data if isinstance(data, list) else (data or {}).get("runs", result.get("runs", []))
        context = target_context(data) if isinstance(data, dict) else {}
        parent_input = data.get("input") if isinstance(data, dict) else None
        mapped = [_map_run({"input": parent_input, **row}, routine_id, context) for row in raw if isinstance(row, dict)]
    return sorted(mapped, key=lambda run: run.started_at or "", reverse=True)
