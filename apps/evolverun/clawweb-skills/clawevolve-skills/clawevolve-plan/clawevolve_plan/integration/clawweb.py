from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from ..constants import DEFAULT_TIMEOUT_SECONDS, clawweb_base_url
from .. import logger

_MAX_JSON_RESPONSE_BYTES = 10 * 1024 * 1024

def _base_url() -> str:
    return clawweb_base_url()


def _report_opener() -> urllib.request.OpenerDirector:
    # Equivalent to curl --noproxy '*' for the step report path.
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _report_url(task_id: str, step_id: str, base_url: str | None = None) -> str:
    root = (base_url or _base_url()).rstrip("/")
    return (
        root
        + "/api/evolve/internal/tasks/"
        + urllib.parse.quote(str(task_id), safe="")
        + "/steps/"
        + urllib.parse.quote(str(step_id), safe="")
        + "/report"
    )


def _preview(value: Any, limit: int = 1200) -> str:
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


def _read_error_response(response: Any) -> tuple[str, bool]:
    payload = response.read(_MAX_JSON_RESPONSE_BYTES + 1)
    truncated = len(payload) > _MAX_JSON_RESPONSE_BYTES
    if truncated:
        payload = payload[:_MAX_JSON_RESPONSE_BYTES]
    return payload.decode("utf-8", errors="replace"), truncated


def _read_limited_response(response: Any) -> bytes:
    payload = response.read(_MAX_JSON_RESPONSE_BYTES + 1)
    if len(payload) > _MAX_JSON_RESPONSE_BYTES:
        raise ClawWebResponseError(
            "POST",
            "step-report",
            int(getattr(response, "status", 0) or 0),
            f"response exceeds {_MAX_JSON_RESPONSE_BYTES} bytes",
            payload[:1200].decode("utf-8", errors="replace"),
        )
    return payload


def _looks_like_auth_gate(
    parsed: Any, headers: dict[str, str] | None = None
) -> tuple[bool, str]:
    headers = headers or {}
    if any(k.lower() == "zt-action-url" for k in headers):
        return True, "response_has_zt_action_url_login_header"
    if not isinstance(parsed, dict):
        return False, ""
    action_type = str(parsed.get("actionType") or "").upper()
    error_code = str(
        parsed.get("buserviceErrorCode") or parsed.get("errorCode") or ""
    ).upper()
    msg = str(
        parsed.get("buserviceErrorMsg")
        or parsed.get("message")
        or parsed.get("help")
        or ""
    )
    if action_type == "LOGIN":
        return True, "response_action_type_login"
    if error_code in {"USER_NOT_LOGIN", "NOT_LOGIN", "LOGIN_REQUIRED", "UNAUTHORIZED"}:
        return True, f"response_error_code_{error_code.lower()}"
    if "pubLogin" in msg or "当前无法识别用户信息" in msg or "身份验证" in msg:
        return True, "response_message_requires_login"
    return False, ""


def _http_status_retryable(status: int) -> bool:
    return status >= 500 or status in {408, 409, 425, 429}


def post_step_report(
    task_id: str,
    step_id: str,
    *,
    status: str,
    summary: str,
    output: dict[str, Any] | None = None,
    progress: dict[str, Any] | None = None,
    error: str | dict[str, Any] | None = None,
    base_url: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"status": status, "summary": summary}
    if output is not None:
        payload["output"] = output
    if progress is not None:
        payload["progress"] = progress
    if error:
        payload["error"] = error

    url = _report_url(task_id, step_id, base_url=base_url)
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(
        "utf-8"
    )
    payload_keys = sorted(payload.keys())
    result: dict[str, Any] = {
        "enabled": True,
        "status": "pending",
        "task_id": task_id,
        "step_id": step_id,
        "url": url,
        "method": "POST",
        "attempts": 0,
        "http_status": None,
        "payload_bytes": len(body),
        "payload_keys": payload_keys,
        "payload_preview": _preview(payload),
    }
    logger.info(
        "clawweb step report prepared",
        task_id=task_id,
        step_id=step_id,
        status=status,
        url=url,
        payload_bytes=len(body),
        payload_keys=payload_keys,
        payload_preview=_preview(payload),
    )
    opener = _report_opener()
    last_error = ""
    for attempt in range(1, 6):
        result["attempts"] = attempt
        req = urllib.request.Request(
            url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        logger.info(
            "clawweb step report attempt start",
            attempt=f"{attempt}/5",
            task_id=task_id,
            step_id=step_id,
            status=status,
            url=url,
            timeout_seconds=DEFAULT_TIMEOUT_SECONDS,
            payload_bytes=len(body),
        )
        try:
            with opener.open(req, timeout=DEFAULT_TIMEOUT_SECONDS) as resp:
                try:
                    payload = _read_limited_response(resp)
                except ClawWebResponseError as exc:
                    result.update({
                        "status": "deferred",
                        "http_status": getattr(resp, "status", None),
                        "error": str(exc),
                        "error_category": "response_contract_error",
                    })
                    return result
                raw = payload.decode("utf-8", errors="replace")
                response_headers = dict(resp.headers.items())
                try:
                    parsed: Any = json.loads(raw) if raw else {}
                except json.JSONDecodeError:
                    result.update({
                        "status": "deferred",
                        "http_status": resp.status,
                        "response_preview": _response_preview(raw),
                        "error": "ClawWeb step report response is not valid JSON",
                        "error_category": "response_contract_error",
                    })
                    return result
                is_auth_gate, auth_gate_reason = _looks_like_auth_gate(
                    parsed, response_headers
                )
                if is_auth_gate:
                    last_error = f"ClawWeb auth gate/login response despite HTTP {resp.status}: {auth_gate_reason}"
                    result.update(
                        {
                            "status": "deferred",
                            "http_status": resp.status,
                            "body": parsed,
                            "response_preview": _response_preview(raw),
                            "error": last_error,
                            "error_category": "auth_gate_or_login_required",
                            "auth_gate_reason": auth_gate_reason,
                            "response_headers": {
                                k: v
                                for k, v in response_headers.items()
                                if k.lower()
                                in {"content-type", "zt-action-url", "server", "via"}
                            },
                        }
                    )
                    logger.warning(
                        "clawweb step report auth gate response",
                        attempt=f"{attempt}/5",
                        task_id=task_id,
                        step_id=step_id,
                        status=status,
                        http_status=resp.status,
                        auth_gate_reason=auth_gate_reason,
                        response_preview=_response_preview(raw),
                    )
                    return result
                if not isinstance(parsed, dict) or parsed.get("ok") is not True:
                    result.update({
                        "status": "deferred",
                        "http_status": resp.status,
                        "body": parsed,
                        "response_preview": _response_preview(raw),
                        "error": (
                            "ClawWeb did not confirm report with ok=true: "
                            + _response_preview(raw)
                        ),
                        "error_category": "response_contract_error",
                    })
                    return result
                result.update(
                    {
                        "status": "ok",
                        "http_status": resp.status,
                        "body": parsed,
                        "response_preview": _response_preview(raw),
                    }
                )
                logger.info(
                    "clawweb step report attempt done",
                    attempt=f"{attempt}/5",
                    task_id=task_id,
                    step_id=step_id,
                    status=status,
                    http_status=resp.status,
                    response_preview=_response_preview(raw),
                )
                return result
        except urllib.error.HTTPError as exc:
            raw, response_truncated = _read_error_response(exc)
            response_headers = dict(exc.headers.items()) if exc.headers else {}
            response_preview = _response_preview(raw)
            last_error = f"HTTPError: HTTP {exc.code}: {raw}"
            result["http_status"] = exc.code
            result["response_preview"] = response_preview
            result["response_truncated"] = response_truncated
            result["response_headers"] = {
                k: v
                for k, v in response_headers.items()
                if k.lower() in {"content-type", "zt-action-url", "server", "via"}
            }
            if exc.code == 404 and ("step 不存在" in raw or "step" in raw.lower()):
                result["error_category"] = "task_or_step_not_found"
                result["likely_reason"] = (
                    "task-id/step-id 不存在、不匹配，或上报环境与创建 task/step 的环境不一致"
                )
            elif 400 <= exc.code < 500:
                result["error_category"] = "http_4xx_no_retry"
                result["likely_reason"] = "client_request_or_route_or_auth_error"
            else:
                result["error_category"] = "http_retryable"
                result["likely_reason"] = "server_or_gateway_retryable_error"
            logger.warning(
                "clawweb step report attempt http_error",
                attempt=f"{attempt}/5",
                task_id=task_id,
                step_id=step_id,
                status=status,
                url=url,
                http_status=exc.code,
                error_category=result["error_category"],
                likely_reason=result.get("likely_reason"),
                response_preview=response_preview,
                response_headers=result.get("response_headers"),
                payload_preview=result.get("payload_preview"),
            )
            if not _http_status_retryable(exc.code):
                result.update({"status": "deferred", "error": last_error})
                return result
        except Exception as exc:  # noqa: BLE001 - report upload must not mask local result.
            last_error = f"{type(exc).__name__}: {exc}"
            result["error_category"] = "network_or_runtime_retryable"
            logger.warning(
                "clawweb step report attempt failed",
                attempt=f"{attempt}/5",
                task_id=task_id,
                step_id=step_id,
                status=status,
                url=url,
                error=last_error,
            )
        if attempt < 5:
            delay = 3
            logger.info(
                "clawweb step report retry sleep",
                attempt=f"{attempt}/5",
                task_id=task_id,
                step_id=step_id,
                delay_seconds=delay,
            )
            time.sleep(delay)
    result.update({"status": "deferred", "error": last_error})
    logger.warning(
        "clawweb step report deferred",
        task_id=task_id,
        step_id=step_id,
        status=status,
        url=url,
        attempts=result.get("attempts"),
        http_status=result.get("http_status"),
        error=last_error,
        response_preview=result.get("response_preview", ""),
    )
    return result


class ClawWebHTTPError(RuntimeError):
    """HTTP failure preserving status/body for reliable control flow."""

    def __init__(self, method: str, path: str, status_code: int, body: str) -> None:
        self.method = method
        self.path = path
        self.status_code = status_code
        self.body = body
        super().__init__(
            f"ClawWeb {method} {path} failed: HTTP {status_code}: {body}"
        )


class ClawWebResponseError(RuntimeError):
    """A successful HTTP response that violates the ClawWeb JSON contract."""

    def __init__(
        self, method: str, path: str, status_code: int, reason: str, body: str
    ) -> None:
        self.method = method
        self.path = path
        self.status_code = status_code
        self.reason = reason
        self.body = body
        super().__init__(
            f"ClawWeb {method} {path} returned an invalid response: HTTP "
            f"{status_code}; {reason}; preview={_response_preview(body)}"
        )


class ClawWebClient:
    def __init__(
        self, user_id: str = "", cookie: str = "", base_url: str | None = None
    ) -> None:
        self.user_id = user_id
        # ClawWeb bench domain/template APIs authenticate with X-User-Id.
        # Keep cookie accepted for backward compatibility, but do not rely on it.
        self.cookie = ""
        self.base_url = (base_url or _base_url()).rstrip("/")
        self.timeout_seconds = DEFAULT_TIMEOUT_SECONDS
        self.retry_attempts = 5
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def _headers(self, content_type: str = "application/json") -> dict[str, str]:
        headers = {"Content-Type": content_type}
        if self.user_id:
            # ClawWeb bench routes authenticate by x-user-id only; cookie is optional.
            headers["X-User-Id"] = self.user_id
        return headers

    def _retry_delay(self, attempt: int) -> float:
        return 3

    def _open_with_retry(
        self,
        req: urllib.request.Request,
        path: str,
        method: str,
        timeout: int | None = None,
    ) -> tuple[int, str]:
        last_error: Exception | None = None
        request_url = self.base_url + path
        request_timeout = timeout or self.timeout_seconds
        body_len = len(req.data or b"") if getattr(req, "data", None) is not None else 0
        for attempt in range(1, self.retry_attempts + 1):
            logger.info(
                "clawweb api attempt start",
                method=method,
                path=path,
                url=request_url,
                attempt=f"{attempt}/{self.retry_attempts}",
                timeout_seconds=request_timeout,
                body_bytes=body_len,
                has_user_id=bool(self.user_id),
            )
            try:
                with self.opener.open(req, timeout=request_timeout) as resp:
                    payload = resp.read(_MAX_JSON_RESPONSE_BYTES + 1)
                    if len(payload) > _MAX_JSON_RESPONSE_BYTES:
                        raise ClawWebResponseError(
                            method,
                            path,
                            resp.status,
                            f"response exceeds {_MAX_JSON_RESPONSE_BYTES} bytes",
                            payload[:1200].decode("utf-8", errors="replace"),
                        )
                    raw = payload.decode("utf-8", errors="replace")
                    logger.info(
                        "clawweb api attempt done",
                        method=method,
                        path=path,
                        url=request_url,
                        attempt=f"{attempt}/{self.retry_attempts}",
                        http_status=resp.status,
                        response_preview=_response_preview(raw),
                    )
                    return resp.status, raw
            except ClawWebResponseError:
                raise
            except urllib.error.HTTPError as exc:
                raw, response_truncated = _read_error_response(exc)
                retryable = _http_status_retryable(exc.code)
                if (
                    exc.code == 409
                    and method == "POST"
                    and path == "/api/bench/domains"
                ):
                    retryable = False
                logger.warning(
                    "clawweb api attempt http_error",
                    method=method,
                    path=path,
                    url=request_url,
                    attempt=f"{attempt}/{self.retry_attempts}",
                    http_status=exc.code,
                    response_preview=_response_preview(raw),
                    response_truncated=response_truncated,
                    retryable=retryable and attempt < self.retry_attempts,
                )
                if not retryable or attempt >= self.retry_attempts:
                    raise ClawWebHTTPError(
                        method, path, exc.code, raw
                    ) from exc
                last_error = exc
                delay = self._retry_delay(attempt)
                logger.info(
                    "clawweb api retry sleep",
                    method=method,
                    path=path,
                    attempt=f"{attempt}/{self.retry_attempts}",
                    delay_seconds=f"{delay:.2f}",
                )
                time.sleep(delay)
            except Exception as exc:  # network/reset/timeout
                logger.warning(
                    "clawweb api attempt failed",
                    method=method,
                    path=path,
                    url=request_url,
                    attempt=f"{attempt}/{self.retry_attempts}",
                    error=f"{type(exc).__name__}: {exc}",
                    retryable=attempt < self.retry_attempts,
                )
                if attempt >= self.retry_attempts:
                    raise RuntimeError(f"ClawWeb {method} {path} error: {exc}") from exc
                last_error = exc
                delay = self._retry_delay(attempt)
                logger.info(
                    "clawweb api retry sleep",
                    method=method,
                    path=path,
                    attempt=f"{attempt}/{self.retry_attempts}",
                    delay_seconds=f"{delay:.2f}",
                )
                time.sleep(delay)
        raise RuntimeError(f"ClawWeb {method} {path} error: {last_error}")

    def _request_json(
        self,
        method: str,
        path: str,
        data: Any | None = None,
        headers: dict[str, str] | None = None,
        timeout: int | None = None,
    ) -> dict[str, Any]:
        body = None if data is None else json.dumps(data).encode("utf-8")
        req = urllib.request.Request(
            self.base_url + path,
            data=body,
            headers=headers or self._headers(),
            method=method,
        )
        status, raw = self._open_with_retry(req, path, method, timeout=timeout)
        parsed = self._decode_json_response(method, path, status, raw)
        return {"status_code": status, "body": parsed}

    @staticmethod
    def _decode_json_response(
        method: str, path: str, status: int, raw: str
    ) -> Any:
        if not raw:
            return {}
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            lowered = raw[:4000].lower()
            reason = "response body is not valid JSON"
            if "<html" in lowered or "<!doctype html" in lowered:
                reason = "received HTML instead of JSON (possible login/auth gateway)"
            raise ClawWebResponseError(method, path, status, reason, raw) from exc
        is_auth_gate, auth_reason = _looks_like_auth_gate(parsed)
        if is_auth_gate:
            raise ClawWebResponseError(
                method,
                path,
                status,
                f"authentication/login response: {auth_reason}",
                raw,
            )
        if not isinstance(parsed, (dict, list)):
            raise ClawWebResponseError(
                method, path, status, "JSON body must be an object or array", raw
            )
        return parsed

    def create_domain(self, domain_id: str, description: str) -> dict[str, Any]:
        result = self._request_json(
            "POST",
            "/api/bench/domains",
            {"domainId": domain_id, "name": domain_id, "description": description},
        )
        _require_domain_response(result, "POST", "/api/bench/domains")
        return result

    def get_domain(self, owner_user_id: str, domain_id: str) -> dict[str, Any]:
        path = (
            "/api/bench/domains/"
            f"{urllib.parse.quote(owner_user_id, safe='')}/"
            f"{urllib.parse.quote(domain_id, safe='')}"
        )
        result = self._request_json("GET", path)
        _require_domain_response(result, "GET", path)
        return result

    def upload_zip(
        self, owner_user_id: str, domain_id: str, zip_path: Path
    ) -> dict[str, Any]:
        """Import a template zip through the current ClawWeb upload API.

        ClawWeb route:
          POST /api/bench/domains/:ownerUserId/:domainId/uploads/scan
        multer field name:
          files
        """
        boundary = f"----clawevolveboundary{int(time.time() * 1000)}"
        data = zip_path.read_bytes()
        body = b"".join(
            [
                f"--{boundary}\r\n".encode(),
                (
                    "Content-Disposition: form-data; "
                    f'name="files"; filename="{zip_path.name}"\r\n'
                ).encode(),
                b"Content-Type: application/zip\r\n\r\n",
                data,
                b"\r\n",
                f"--{boundary}--\r\n".encode(),
            ]
        )
        path = (
            "/api/bench/domains/"
            f"{urllib.parse.quote(owner_user_id, safe='')}/"
            f"{urllib.parse.quote(domain_id, safe='')}/uploads/scan"
        )
        req = urllib.request.Request(
            self.base_url + path,
            data=body,
            headers=self._headers(f"multipart/form-data; boundary={boundary}"),
            method="POST",
        )
        status, raw = self._open_with_retry(
            req,
            path,
            "POST",
            timeout=max(self.timeout_seconds, DEFAULT_TIMEOUT_SECONDS),
        )
        return {
            "status_code": status,
            "body": self._decode_json_response("POST", path, status, raw),
        }

    def batch_publish(
        self, owner_user_id: str, domain_id: str, templates: list[str]
    ) -> dict[str, Any]:
        path = (
            "/api/bench/domains/"
            f"{urllib.parse.quote(owner_user_id, safe='')}/"
            f"{urllib.parse.quote(domain_id, safe='')}/templates/batch-publish"
        )
        return self._request_json(
            "POST",
            path,
            {"templates": [{"templateName": name} for name in templates]},
            timeout=DEFAULT_TIMEOUT_SECONDS,
        )

    def list_published_templates(
        self, owner_user_id: str, domain_id: str
    ) -> dict[str, Any]:
        path = (
            "/api/bench/domains/"
            f"{urllib.parse.quote(owner_user_id, safe='')}/"
            f"{urllib.parse.quote(domain_id, safe='')}/templates?status=published"
        )
        return self._request_json("GET", path, timeout=DEFAULT_TIMEOUT_SECONDS)


def _require_object_response(
    result: dict[str, Any], method: str, path: str
) -> dict[str, Any]:
    body = result.get("body")
    if not isinstance(body, dict) or not body:
        raw = json.dumps(body, ensure_ascii=False)
        raise ClawWebResponseError(
            method,
            path,
            int(result.get("status_code") or 0),
            "JSON body must be a non-empty object for this endpoint",
            raw,
        )
    return body


def _require_domain_response(
    result: dict[str, Any], method: str, path: str
) -> dict[str, Any]:
    body = _require_object_response(result, method, path)
    effective = body.get("data") if isinstance(body.get("data"), dict) else body
    recognized = any(
        str(effective.get(key) or "").strip()
        for key in ("ownerUserId", "domainId", "id", "name", "status")
    )
    if not recognized:
        raise ClawWebResponseError(
            method,
            path,
            int(result.get("status_code") or 0),
            "domain response contains no recognizable domain metadata",
            json.dumps(body, ensure_ascii=False),
        )
    return effective


def _body(result: dict[str, Any]) -> dict[str, Any]:
    body = result.get("body")
    if not isinstance(body, dict):
        return {}
    data = body.get("data")
    return data if isinstance(data, dict) else body


def _body_list(result: dict[str, Any]) -> list[Any]:
    body = result.get("body")
    if isinstance(body, list):
        return body
    if not isinstance(body, dict):
        return []
    for key in ("items", "templates"):
        if isinstance(body.get(key), list):
            return body[key]
    data = body.get("data")
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("items", "templates"):
            if isinstance(data.get(key), list):
                return data[key]
    return []


def _imported_template_names(upload_result: dict[str, Any]) -> list[str]:
    names: list[str] = []
    for item in _body_list(upload_result):
        if not isinstance(item, dict):
            continue
        if not item.get("imported"):
            continue
        name = str(item.get("templateName") or "").strip()
        if name and name not in names:
            names.append(name)
    return names


def _scanned_template_names(upload_result: dict[str, Any]) -> list[str]:
    """Return every template name recognized by the upload scan.

    Scan actions describe whether ClawWeb imported, skipped, or conflicted with a
    template. They do not change the identity of the local package. Exact-set
    validation therefore uses all recognized names, while publish selection
    remains action-aware.
    """
    names: list[str] = []
    for item in _body_list(upload_result):
        if not isinstance(item, dict):
            continue
        name = str(item.get("templateName") or "").strip()
        if name and name not in names:
            names.append(name)
    return names


def _template_hashes(result: dict[str, Any]) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for item in _body_list(result):
        if not isinstance(item, dict):
            continue
        name = str(item.get("templateName") or "").strip()
        source_hash = str(item.get("sourceHash") or "").strip().lower()
        if name and source_hash:
            hashes[name] = source_hash
    return hashes


def _publish_candidate_names(upload_result: dict[str, Any]) -> list[str]:
    """Names that should be published after uploads/scan.

    A re-run after a successful scan but failed publish can return action=skip
    because the identical draft already exists. Those skipped items still need a
    batch-publish attempt, otherwise the flow gets stuck at uploaded_no_imports.
    Conflicts are excluded because ClawWeb intentionally did not import them.
    """
    names: list[str] = []
    for item in _body_list(upload_result):
        if not isinstance(item, dict):
            continue
        action = str(item.get("action") or "")
        if action == "conflict":
            continue
        if not item.get("imported") and action != "skip":
            continue
        name = str(item.get("templateName") or "").strip()
        if name and name not in names:
            names.append(name)
    return names


def _created_owner_user_id(create_result: dict[str, Any], fallback: str) -> str:
    return str(_body(create_result).get("ownerUserId") or fallback)


def _published_template_names(list_result: dict[str, Any]) -> list[str]:
    names: list[str] = []
    for item in _body_list(list_result):
        if not isinstance(item, dict):
            continue
        name = str(item.get("templateName") or "").strip()
        if name and name not in names:
            names.append(name)
    return names


def upload_templates(
    domain_id: str,
    zip_path: Path,
    user_id: str,
    cookie: str = "",
    *,
    expected_template_names: list[str] | None = None,
    expected_template_hashes: dict[str, str] | None = None,
) -> dict[str, Any]:
    # Single-user-id mode: the same user_id is used as X-User-Id and URL ownerUserId.
    client = ClawWebClient(user_id=user_id)
    try:
        zip_bytes = zip_path.stat().st_size
    except OSError:
        zip_bytes = -1
    logger.info(
        "clawweb bench upload flow start",
        base_url=client.base_url,
        domain_id=domain_id,
        user_id=user_id,
        zip_path=zip_path,
        zip_bytes=zip_bytes,
    )
    result: dict[str, Any] = {
        "enabled": True,
        "status": "pending",
        "base_url": client.base_url,
        "user_id": user_id,
        "owner_user_id": user_id,
        "domain_id": domain_id,
        "zip_path": str(zip_path),
        "published": False,
        "verified": False,
        "expected_template_names": list(expected_template_names or []),
        "expected_template_count": len(expected_template_names or []),
        "expected_template_hashes": dict(expected_template_hashes or {}),
    }

    try:
        logger.info("clawweb create domain start", domain_id=domain_id, user_id=user_id)
        created = client.create_domain(
            domain_id,
            f"Auto-generated Bench Domain for clawevolve-plan run {domain_id}",
        )
        logger.info(
            "clawweb create domain done",
            domain_id=domain_id,
            status_code=created.get("status_code"),
            body_preview=_preview(created.get("body")),
        )
    except ClawWebHTTPError as exc:
        # Stable split domain IDs intentionally collide on retries. Reuse only
        # the explicit create-domain conflict, never a message-shaped error.
        if exc.status_code != 409:
            logger.warning(
                "clawweb create domain failed",
                domain_id=domain_id,
                error=f"{type(exc).__name__}: {exc}",
            )
            raise
        logger.warning(
            "clawweb create domain conflict; fetching existing domain",
            domain_id=domain_id,
            user_id=user_id,
            error=f"{type(exc).__name__}: {exc}",
        )
        created = client.get_domain(user_id, domain_id)
        created["already_exists"] = True
        logger.info(
            "clawweb get existing domain done",
            domain_id=domain_id,
            status_code=created.get("status_code"),
            body_preview=_preview(created.get("body")),
        )
    domain_status = str(_body(created).get("status") or "").strip().lower()
    if domain_status and domain_status != "active":
        raise RuntimeError(
            f"ClawWeb Domain {domain_id} is not active: status={domain_status}"
        )
    result["create_domain"] = created
    result["domain_status"] = domain_status or "active"
    owner_user_id = _created_owner_user_id(created, user_id)
    result["owner_user_id"] = owner_user_id

    logger.info(
        "clawweb upload zip start",
        owner_user_id=owner_user_id,
        domain_id=domain_id,
        zip_path=zip_path,
        zip_bytes=zip_bytes,
    )
    uploaded = client.upload_zip(owner_user_id, domain_id, zip_path)
    logger.info(
        "clawweb upload zip done",
        owner_user_id=owner_user_id,
        domain_id=domain_id,
        status_code=uploaded.get("status_code"),
        body_preview=_preview(uploaded.get("body")),
    )
    result["upload"] = uploaded
    imported_templates = _imported_template_names(uploaded)
    scanned_templates = _scanned_template_names(uploaded)
    publish_candidates = _publish_candidate_names(uploaded)
    expected = list(
        dict.fromkeys(
            str(name).strip()
            for name in (expected_template_names or [])
            if str(name).strip()
        )
    )
    expected_hashes = {
        str(name).strip(): str(value).strip().lower()
        for name, value in (expected_template_hashes or {}).items()
        if str(name).strip() and str(value).strip()
    }
    scanned_hashes = _template_hashes(uploaded)
    scanned_names = set(scanned_templates)
    expected_names = set(expected)
    missing_from_scan = [name for name in expected if name not in scanned_names]
    unexpected_from_scan = [
        name for name in scanned_templates if expected and name not in expected_names
    ]
    scan_hash_mismatches = {
        name: {"expected": expected_hash, "actual": scanned_hashes.get(name, "")}
        for name, expected_hash in expected_hashes.items()
        if scanned_hashes.get(name, "") != expected_hash
    }
    logger.info(
        "clawweb upload parsed",
        domain_id=domain_id,
        imported_templates=imported_templates,
        scanned_templates=scanned_templates,
        publish_candidates=publish_candidates,
        expected_templates=expected,
        missing_from_scan=missing_from_scan,
        unexpected_from_scan=unexpected_from_scan,
        scan_hash_mismatches=scan_hash_mismatches,
    )
    result["imported_templates"] = imported_templates
    result["scanned_templates"] = scanned_templates
    result["publish_candidates"] = publish_candidates
    result["missing_from_scan"] = missing_from_scan
    result["unexpected_from_scan"] = unexpected_from_scan
    result["scanned_template_hashes"] = scanned_hashes
    result["scan_hash_mismatches"] = scan_hash_mismatches

    if missing_from_scan or unexpected_from_scan or scan_hash_mismatches:
        result["status"] = "uploaded_template_set_mismatch"
        return result

    publish_failed = False
    if publish_candidates:
        logger.info(
            "clawweb batch publish start",
            owner_user_id=owner_user_id,
            domain_id=domain_id,
            publish_candidates=publish_candidates,
        )
        try:
            published = client.batch_publish(
                owner_user_id, domain_id, publish_candidates
            )
        except Exception as exc:  # verification below is the authoritative state.
            publish_failed = True
            result["batch_publish_error"] = f"{type(exc).__name__}: {exc}"
            result["status"] = "uploaded_publish_uncertain"
            logger.warning(
                "clawweb batch publish outcome uncertain; verify remote state",
                owner_user_id=owner_user_id,
                domain_id=domain_id,
                error=result["batch_publish_error"],
            )
        else:
            logger.info(
                "clawweb batch publish done",
                owner_user_id=owner_user_id,
                domain_id=domain_id,
                status_code=published.get("status_code"),
                body_preview=_preview(published.get("body")),
            )
            result["batch_publish"] = published
            publish_body = _body(published)
            failed = int(publish_body.get("failed") or 0)
            succeeded = int(publish_body.get("published") or 0)
            result["publish_reported_count"] = succeeded
            publish_failed = failed > 0
            if publish_failed:
                result["status"] = "uploaded_publish_failed"

    # Verification is authoritative. Always run it even when batch-publish
    # reports failures: a prior timed-out attempt may already have published all
    # templates, making a retry return "No draft version" for every item.
    logger.info(
        "clawweb verify published start",
        owner_user_id=owner_user_id,
        domain_id=domain_id,
        expected_templates=expected,
    )
    verified = client.list_published_templates(owner_user_id, domain_id)
    logger.info(
        "clawweb verify published done",
        owner_user_id=owner_user_id,
        domain_id=domain_id,
        status_code=verified.get("status_code"),
        body_preview=_preview(verified.get("body")),
    )
    result["verify_published"] = verified
    published_names = set(_published_template_names(verified))
    published_hashes = _template_hashes(verified)
    required_names = expected or scanned_templates
    required_name_set = set(required_names)
    missing = [name for name in required_names if name not in published_names]
    unexpected = sorted(published_names.difference(required_name_set))
    published_hash_mismatches = {
        name: {"expected": expected_hash, "actual": published_hashes.get(name, "")}
        for name, expected_hash in expected_hashes.items()
        if published_hashes.get(name, "") != expected_hash
    }
    result["verified_templates"] = sorted(
        published_names.intersection(required_name_set)
    )
    result["missing_published_templates"] = missing
    result["unexpected_published_templates"] = unexpected
    result["published_template_hashes"] = published_hashes
    result["published_hash_mismatches"] = published_hash_mismatches
    result["verified"] = (
        bool(required_names)
        and not missing
        and not unexpected
        and not published_hash_mismatches
    )
    result["published"] = result["verified"]
    result["batch_publish_reported_failure"] = publish_failed
    result["status"] = (
        "published" if result["verified"] else "published_verify_failed"
    )
    logger.info(
        "clawweb bench upload flow done",
        domain_id=domain_id,
        status=result.get("status"),
        published=result.get("published"),
        verified=result.get("verified"),
        owner_user_id=result.get("owner_user_id"),
        imported_count=len(result.get("imported_templates") or []),
        publish_candidate_count=len(result.get("publish_candidates") or []),
    )
    return result


def verify_published_domain(
    domain_id: str,
    user_id: str,
    *,
    owner_user_id: str = "",
    expected_template_hashes: dict[str, str],
) -> dict[str, Any]:
    """Verify a cached Domain against current ClawWeb authoritative state."""
    client = ClawWebClient(user_id=user_id)
    owner = owner_user_id or user_id
    domain = client.get_domain(owner, domain_id)
    domain_body = _body(domain)
    domain_status = str(domain_body.get("status") or "").strip().lower()
    listed = client.list_published_templates(owner, domain_id)
    actual_hashes = _template_hashes(listed)
    expected_hashes = {
        str(name).strip(): str(value).strip().lower()
        for name, value in expected_template_hashes.items()
        if str(name).strip() and str(value).strip()
    }
    expected_names = set(expected_hashes)
    actual_names = set(_published_template_names(listed))
    missing = sorted(expected_names - actual_names)
    unexpected = sorted(actual_names - expected_names)
    hash_mismatches = {
        name: {"expected": digest, "actual": actual_hashes.get(name, "")}
        for name, digest in expected_hashes.items()
        if actual_hashes.get(name, "") != digest
    }
    verified = (
        domain_status == "active"
        and bool(expected_names)
        and not missing
        and not unexpected
        and not hash_mismatches
    )
    return {
        "status": "verified" if verified else "verification_failed",
        "verified": verified,
        "domain_status": domain_status,
        "domain": domain,
        "published_templates": listed,
        "expected_template_hashes": expected_hashes,
        "published_template_hashes": actual_hashes,
        "missing_published_templates": missing,
        "unexpected_published_templates": unexpected,
        "published_hash_mismatches": hash_mismatches,
    }
