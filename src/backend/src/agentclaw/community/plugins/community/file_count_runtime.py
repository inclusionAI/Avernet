"""Fixed-instance Engine HTTP file counting, without fallback enumeration."""
import asyncio
import json
from dataclasses import asdict
from time import monotonic

from agentclaw.community.kernel.file_count import FileCountError, safe_log_fields
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.device_adapter_transport import (
    DeviceAdapterHTTPStatusError, DeviceAdapterEndpointNotFoundError,
)
from agentclaw.community.plugin_api.http_client import HttpClientTimeoutError

logger = get_logger()
ENDPOINT = "/api/file/count"
INSTANCE_TIMEOUT_SECONDS = 30
ERROR_CODES = {
    400: {"invalid_path", "not_directory"},
    403: {"path_forbidden", "permission_denied"},
    404: {"path_not_found"},
    408: {"scan_timeout"},
    409: {"directory_changed"},
    501: {"unsupported"},
    503: {"busy"},
    500: {"scan_failed"},
}


def _error_code(status, payload):
    # COSEC: only allowlisted error codes cross the untrusted Engine boundary.
    detail = payload.get("detail", payload) if isinstance(payload, dict) else {}
    if isinstance(detail, dict):
        code = detail.get("error_code") or detail.get("code") or detail.get("message")
    else:
        code = detail
    if isinstance(code, str) and code in ERROR_CODES.get(status, set()):
        return code
    return "unsupported" if status in {404, 501} else "engine_call_failed"


class HttpFileCountRuntime:
    def __init__(self, baas, resolver, transport, http):
        self.baas, self.resolver, self.transport, self.http = baas, resolver, transport, http

    async def targets(self, binding):
        if binding.device_provider == "arca":
            return [binding.device_id] if binding.device_id else []
        devices = await asyncio.to_thread(self.baas.list_devices_by_bot_uuid, binding.device_id)
        if not isinstance(devices, list) or any(not isinstance(d, dict) for d in devices):
            raise FileCountError("invalid_device_snapshot")
        targets = [d["device_uuid"] if "device_uuid" in d else d.get("uuid") for d in devices]
        if any(not isinstance(target, str) or not target.strip() for target in targets):
            raise FileCountError("invalid_device_snapshot")
        if len(set(targets)) != len(targets):
            raise FileCountError("invalid_device_snapshot")
        return targets

    async def query(self, binding, target, query, operator_id):
        started = monotonic()
        result = {"provider": binding.device_provider, "binding_id": binding.id,
                  "instance_id": target, "path": query.path, "file_count": None}
        fields = {**asdict(query), **result, "engine_request_id": query.request_id,
                  "operator_id": operator_id, "method": "GET", "route": ENDPOINT,
                  "system": "engine", "direction": "outbound", "elapsed_ms": 0}
        logger.info("backend.file_count.engine_request %s", safe_log_fields(fields))
        try:
            async with asyncio.timeout(INSTANCE_TIMEOUT_SECONDS):
                # COSEC: resolve trusted connections and pin each BaaS device UUID.
                ctx = await asyncio.to_thread(
                    self.resolver.resolve_for_binding_invoke,
                    binding.id, operator_id, bot_id=query.bot_id,
                    device_uuid=target if binding.device_provider == "baas" else None,
                )
                params = {"path": query.path, "request_id": query.request_id}
                if binding.device_provider == "baas":
                    response = await self.transport.invoke(
                        ctx.conn_info, "GET", ENDPOINT, params=params,
                        timeout=INSTANCE_TIMEOUT_SECONDS,
                    )
                else:
                    # COSEC: caller path is only a query value, never the destination URL.
                    raw = await asyncio.to_thread(
                        self.http.get, ctx.conn_info["url"].rstrip("/") + ENDPOINT,
                        params=params, headers=ctx.conn_info.get("headers", {}),
                        timeout=INSTANCE_TIMEOUT_SECONDS,
                    )
                    response = raw.json()
                    if raw.status_code >= 400:
                        raise FileCountError(_error_code(raw.status_code, response))
                if not isinstance(response, dict) or response.get("success") is not True:
                    raise FileCountError("invalid_engine_response")
                data = response.get("data")
                if (not isinstance(data, dict) or not isinstance(data.get("path"), str)
                    or not data["path"] or type(data.get("file_count")) is not int
                    or data["file_count"] < 0 or type(data.get("elapsed_ms")) is not int
                    or data["elapsed_ms"] < 0):
                    raise FileCountError("invalid_engine_response")
                result.update(status="success", path=data["path"], file_count=data["file_count"])
        except (DeviceAdapterHTTPStatusError, DeviceAdapterEndpointNotFoundError) as exc:
            try:
                payload = json.loads(exc.response_text)
            except (ValueError, TypeError):
                payload = {}
            code = _error_code(exc.status_code, payload)
            result.update(status="timeout" if code == "scan_timeout" else "failed", error_code=code)
        except Exception as exc:
            timeout = isinstance(exc, (TimeoutError, HttpClientTimeoutError))
            code = "timeout" if timeout else (exc.code if isinstance(exc, FileCountError) else "engine_call_failed")
            result.update(status="timeout" if timeout or code == "scan_timeout" else "failed", error_code=code)
        result["elapsed_ms"] = int((monotonic() - started) * 1000)
        # COSEC: log the validated result projection, never connection/headers/raw payload.
        event = "response" if result["status"] == "success" else "failure"
        logger.info("backend.file_count.engine_%s %s", event, safe_log_fields({**fields, **result}))
        return result
