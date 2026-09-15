from __future__ import annotations

import json
import logging
import os
import random
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from .. import logger as diag_logger

logger = logging.getLogger(__name__)

DEFAULT_CLAWWEB_BASE_URL = "http://127.0.0.1:5173"
DEFAULT_CLAWWEB_UPLOAD_RETRIES = 3


def clawweb_base_url() -> str:
    return (os.environ.get("CLAWEVOLVE_CLAWWEB_URL") or os.environ.get("CLAWWEB_URL") or DEFAULT_CLAWWEB_BASE_URL).rstrip("/")


def post_step_report(
    task_id: str,
    step_id: str,
    *,
    status: str,
    summary: str,
    output: dict[str, Any] | None = None,
    progress: dict[str, Any] | None = None,
    error: str | dict[str, Any] | None = None,
) -> dict[str, Any]:
    """POST one ClawWeb task-step report and return a safe result summary.

    Contract:
    POST /api/evolve/internal/tasks/{task_id}/steps/{step_id}/report
    Body: status, summary, and optionally output/progress/error.  Upload
    failures are retried but must never mask the local diagnose result.
    """

    tid = str(task_id or "").strip()
    sid = str(step_id or "").strip()
    if not tid:
        return {"enabled": False, "status": "skipped", "reason": "empty_task_id"}
    if not sid:
        return {"enabled": False, "status": "skipped", "reason": "empty_step_id"}

    base_url = clawweb_base_url()
    url = (
        f"{base_url}/api/evolve/internal/tasks/"
        f"{urllib.parse.quote(tid, safe='')}/steps/{urllib.parse.quote(sid, safe='')}/report"
    )
    try:
        payload = _build_step_report_payload(
            status=status,
            summary=summary,
            output=output,
            progress=progress,
            error=error,
        )
    except Exception as exc:  # noqa: BLE001 - report contract errors without masking diagnose.
        safe_error = f"{type(exc).__name__}: {exc}"
        diag_logger.warning(
            "clawweb step report payload invalid",
            task_id=tid,
            step_id=sid,
            status=status,
            error=safe_error,
        )
        return {
            "enabled": True,
            "status": "invalid_payload",
            "task_id": tid,
            "step_id": sid,
            "error": safe_error,
        }

    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    diag_logger.info(
        "clawweb step report prepared",
        task_id=tid,
        step_id=sid,
        status=payload.get("status"),
        url=url,
        payload_bytes=len(data),
        payload_keys=sorted(payload.keys()),
        has_progress=progress is not None,
        has_output=output is not None,
        has_error=error is not None,
        payload_preview=_preview_json(payload, 2500),
    )
    timeout = 600.0
    retries = DEFAULT_CLAWWEB_UPLOAD_RETRIES
    attempts = 1 + retries
    base_delay = 1.0
    # Equivalent to curl --noproxy '*': do not inherit environment proxy config.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    last_error: dict[str, Any] | None = None

    for attempt in range(1, attempts + 1):
        diag_logger.info(
            "clawweb step report attempt start",
            task_id=tid,
            step_id=sid,
            status=payload.get("status"),
            attempt=f"{attempt}/{attempts}",
            url=url,
            timeout_seconds=timeout,
        )
        req = urllib.request.Request(
            url,
            data=data,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            with opener.open(req, timeout=timeout) as resp:  # noqa: S310 - internal configured endpoint.
                body = resp.read().decode("utf-8", errors="replace")
                parsed = _parse_response_body(body)
                _raise_if_clawweb_rejected(parsed)
                result = {
                    "enabled": True,
                    "status": "posted",
                    "http_status": resp.status,
                    "task_id": tid,
                    "step_id": sid,
                    "report_status": payload.get("status"),
                    "attempts": attempt,
                    "retries": attempt - 1,
                    "payload_bytes": len(data),
                    "response": parsed,
                }
                diag_logger.info(
                    "clawweb step report posted",
                    task_id=tid,
                    step_id=sid,
                    status=payload.get("status"),
                    http_status=resp.status,
                    attempts=attempt,
                    payload_bytes=len(data),
                    response_preview=_preview_json(parsed, 1500),
                )
                return result
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            category, likely_reason, retryable = _classify_http_error(exc.code, raw)
            last_error = _deferred_result(
                tid,
                sid,
                str(payload.get("status") or ""),
                attempt,
                len(data),
                raw or str(exc),
                http_status=exc.code,
                error_category=category,
                likely_reason=likely_reason,
                retryable=retryable,
                response_preview=_response_preview(raw),
            )
            diag_logger.warning(
                "clawweb step report attempt http_error",
                task_id=tid,
                step_id=sid,
                status=payload.get("status"),
                attempt=f"{attempt}/{attempts}",
                url=url,
                http_status=exc.code,
                error_category=category,
                likely_reason=likely_reason,
                retryable=retryable,
                response_preview=_response_preview(raw),
                payload_preview=_preview_json(payload, 1500),
            )
            if not retryable:
                diag_logger.warning(
                    "clawweb step report deterministic http error; stop retry",
                    task_id=tid,
                    step_id=sid,
                    status=payload.get("status"),
                    http_status=exc.code,
                    error_category=category,
                    likely_reason=likely_reason,
                )
                break
            if attempt >= attempts:
                break
            _sleep_before_retry("clawweb step report", attempt, attempts, base_delay, f"HTTP {exc.code}: {last_error['error']}")
        except Exception as exc:  # noqa: BLE001 - reporting must not mask diagnose result.
            last_error = _deferred_result(
                tid,
                sid,
                str(payload.get("status") or ""),
                attempt,
                len(data),
                f"{type(exc).__name__}: {exc}",
            )
            diag_logger.warning(
                "clawweb step report attempt failed",
                task_id=tid,
                step_id=sid,
                status=payload.get("status"),
                attempt=f"{attempt}/{attempts}",
                error=last_error.get("error"),
            )
            if attempt >= attempts:
                break
            _sleep_before_retry("clawweb step report", attempt, attempts, base_delay, last_error["error"])

    assert last_error is not None
    diag_logger.warning(
        "clawweb step report deferred",
        task_id=tid,
        step_id=sid,
        status=payload.get("status"),
        http_status=last_error.get("http_status"),
        attempts=last_error.get("attempts"),
        payload_bytes=len(data),
        error=str(last_error.get("error", ""))[:300],
        error_category=last_error.get("error_category"),
        likely_reason=last_error.get("likely_reason"),
        retryable=last_error.get("retryable"),
        response_preview=last_error.get("response_preview"),
    )
    return last_error


def _build_step_report_payload(
    *,
    status: str,
    summary: str,
    output: dict[str, Any] | None,
    progress: dict[str, Any] | None,
    error: str | dict[str, Any] | None,
) -> dict[str, Any]:
    normalized_status = str(status or "").strip().lower()
    if normalized_status not in {"accepted", "running", "succeeded", "failed", "canceled"}:
        raise ValueError(f"unsupported ClawWeb report status: {status}")
    payload: dict[str, Any] = {
        "status": normalized_status,
        "summary": str(summary or ""),
    }
    if progress is not None:
        if not isinstance(progress, dict):
            raise ValueError("progress must be a JSON object")
        payload["progress"] = progress
    if output is not None:
        if not isinstance(output, dict):
            raise ValueError("output must be a JSON object")
        payload["output"] = output
    if error is not None:
        payload["error"] = error
    if normalized_status == "succeeded" and not isinstance(payload.get("output"), dict):
        raise ValueError("succeeded ClawWeb reports must carry a JSON object output")
    return payload


def _preview_json(value: Any, limit: int = 2000) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    except Exception:
        text = str(value)
    if len(text) > limit:
        return text[:limit] + f"...<truncated {len(text) - limit} chars>"
    return text


def _response_preview(raw: str, limit: int = 1200) -> str:
    text = " ".join(str(raw or "").split())
    if len(text) > limit:
        return text[:limit] + f"...<truncated {len(text) - limit} chars>"
    return text


def _parse_response_body(body: str) -> Any:
    if not body:
        return {}
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        return {"raw": body[:1000]}


def _raise_if_clawweb_rejected(parsed: Any) -> None:
    if not isinstance(parsed, dict) or parsed.get("ok") is not True:
        raise RuntimeError(
            "ClawWeb did not confirm report with ok=true: "
            f"{json.dumps(parsed, ensure_ascii=False)[:1000]}"
        )


def _classify_http_error(http_status: int, raw_body: str) -> tuple[str, str, bool]:
    body = str(raw_body or "")
    if http_status == 409 and "已处于终态" in body:
        return (
            "step_already_terminal",
            "ClawWeb step 已经处于 succeeded/failed/canceled 等终态；通常是同一个 step_id 被重复执行、或平台/前序异常已先把 step 标记为 failed。终态 step 不能再改成 succeeded。",
            False,
        )
    if http_status == 404:
        return (
            "task_or_step_not_found",
            "task-id/step-id 不存在、不匹配，或上报环境与创建 task/step 的环境不一致。",
            False,
        )
    if 400 <= http_status < 500:
        return (
            "http_4xx_no_retry",
            "客户端请求、路由、认证、状态机冲突或参数问题；重试通常不会改变结果。",
            False,
        )
    return ("http_retryable", "服务端或网关临时错误，可重试。", True)


def _deferred_result(
    task_id: str,
    step_id: str,
    report_status: str,
    attempt: int,
    payload_bytes: int,
    error: str,
    *,
    http_status: int | None = None,
    error_category: str = "",
    likely_reason: str = "",
    retryable: bool | None = None,
    response_preview: str = "",
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "enabled": True,
        "status": "deferred",
        "task_id": task_id,
        "step_id": step_id,
        "report_status": report_status,
        "attempts": attempt,
        "retries": max(0, attempt - 1),
        "payload_bytes": payload_bytes,
        "error": str(error)[:1000],
    }
    if http_status is not None:
        result["http_status"] = http_status
    if error_category:
        result["error_category"] = error_category
    if likely_reason:
        result["likely_reason"] = likely_reason
    if retryable is not None:
        result["retryable"] = retryable
    if response_preview:
        result["response_preview"] = response_preview
    return result


def _sleep_before_retry(prefix: str, attempt: int, attempts: int, base_delay: float, error: str) -> None:
    delay = max(0.1, base_delay) * (2 ** (attempt - 1)) + random.uniform(0, 0.25)
    logger.warning("%s transient error attempt=%d/%d retry_in=%.2fs error=%s", prefix, attempt, attempts, delay, error)
    time.sleep(delay)
