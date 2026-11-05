"""Routines-local relay diagnostics and fixed public error translation."""

from time import monotonic

from fastapi import HTTPException

from agentclaw.community.core.cron.errors import CronApiTimeoutError, CronRelayError
from agentclaw.community.log import get_logger
from agentclaw.community.adapters.http.openapi_v1.responses import trace_id

from .converter import _map_routine, _map_run, failed_targets, target_context, safe_reason

logger = get_logger()


def _job_log(data: dict) -> dict:
    """Project declared business fields; exclude authentication extensions."""
    projected = _map_routine(data).model_dump()
    for key in ("sessionKey", "session_key", "output", "status", "run_id", "started_at", "finished_at", "started_at_ms", "finished_at_ms", "duration_ms", "duration_secs", "job_id", "ran", "reason"):
        value = data.get(key)
        if isinstance(value, (str, int, float, bool)):
            projected[key] = safe_reason(value) if key == "reason" else value
    if isinstance(data.get("runs"), list):
        projected["runs"] = [
            {**_map_run(row, "").model_dump(exclude={"input", "output", "error", "messages"}), "started_at_ms": row.get("started_at_ms"),
             "finished_at_ms": row.get("finished_at_ms"), "duration_ms": row.get("duration_ms"),
             "output": row.get("output")}
            for row in data["runs"] if isinstance(row, dict)
        ]
    return projected


def _result_log(result: dict) -> dict:
    data = result.get("data")
    projected = {"success": result.get("success", True), "error_code": result.get("error_code"),
                 "failed_targets": [row.model_dump() for row in failed_targets(result)]}
    if isinstance(data, list):
        projected["data"] = [_job_log(row) for row in data if isinstance(row, dict)]
    elif isinstance(data, dict):
        projected["data"] = _job_log(data)
        if isinstance(data.get("results"), list):
            projected["results"] = [
                {**target_context(row), "success": row.get("success"),
                 "data": _job_log(row["data"]) if isinstance(row.get("data"), dict) else None}
                for row in data["results"] if isinstance(row, dict)
            ]
        if isinstance(data.get("runs"), list):
            projected["runs"] = [_job_log(row) for row in data["runs"] if isinstance(row, dict)]
    return projected


async def call_cron(operation, *, request, actor, check_success=True, **kwargs):
    started = monotonic()
    fields = {key: value for key, value in kwargs.items() if key not in {"body", "nick_name"}}
    fields.update(operation=operation.__name__, method=request.method, route=request.url.path,
                  request_id=trace_id(request),
                  principal_type=actor.principal_type if actor else "user",
                  app_id=actor.app_id if actor else None)
    logger.info("openapi.routines.request system=backend fields=%s body=%s", fields, kwargs.get("body"))
    try:
        result = await operation(**kwargs)
    except Exception as exc:
        logger.warning("openapi.routines.failure system=backend fields=%s exception_type=%s error_code=%s elapsed_ms=%.1f",
                       fields, type(exc).__name__, getattr(exc, "error_code", None),
                       (monotonic() - started) * 1000)
        if isinstance(exc, CronRelayError) and not isinstance(exc, CronApiTimeoutError) and exc.error_code in {404, 504}:
            raise HTTPException(exc.error_code, "Cron runtime target unavailable") from None
        if isinstance(exc, CronApiTimeoutError):
            raise
        # COSEC: the public exception logger also renders cause chains. Replace
        # upstream exception text here and suppress its original cause locally.
        code = exc.error_code if isinstance(exc, CronRelayError) else 502
        raise CronRelayError("Cron relay request failed", error_code=code) from None
    success = isinstance(result, dict) and result.get("success", True)
    logger.info("openapi.routines.%s system=backend fields=%s result=%s elapsed_ms=%.1f",
                "success" if success else "failure", fields,
                _result_log(result) if isinstance(result, dict) else None,
                (monotonic() - started) * 1000)
    if check_success and isinstance(result, dict) and result.get("success") is False:
        code = result.get("error_code")
        if code in {404, 504}:
            raise HTTPException(code, "Cron runtime target unavailable")
        raise CronRelayError("Cron relay request failed", error_code=code if code in {400, 403, 409} else 502)
    return result
