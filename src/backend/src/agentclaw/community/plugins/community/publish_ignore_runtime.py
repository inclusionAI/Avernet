"""Publish-ignore transport using fixed BaaS replicas or an ARCA binding."""

import asyncio
import re
import time
from dataclasses import asdict
from uuid import uuid4

from agentclaw.community.kernel.publish_ignore import PublishIgnoreError
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.device_adapter_transport import (
    DeviceAdapterTimeoutError,
)
from agentclaw.community.plugin_api.http_client import HttpClientTimeoutError

logger = get_logger()
ENDPOINT = "/api/bot/publish-ignore"


class HttpPublishIgnoreRuntime:
    def __init__(self, baas, resolver, transport, http):
        self.baas, self.resolver, self.transport, self.http = (
            baas,
            resolver,
            transport,
            http,
        )

    async def targets(self, binding):
        if binding.device_provider == "arca":
            return [binding.device_id]
        devices = await asyncio.to_thread(
            self.baas.list_devices_by_bot_uuid, binding.device_id
        )
        targets = [str(d.get("device_uuid") or d.get("uuid") or "") for d in devices]
        if any(not target for target in targets) or len(set(targets)) != len(targets):
            raise PublishIgnoreError("invalid_device_snapshot")
        return targets

    async def query(self, binding, target, query, operator_id):
        started = time.monotonic()
        result = {
            "provider": binding.device_provider, "binding_id": binding.id, "target_id": target,
        }
        engine_request_id = str(uuid4())
        fields = {
            **asdict(query), **result, "engine_request_id": engine_request_id,
            "operator_id": operator_id, "method": "GET", "route": ENDPOINT,
            "system": "engine", "direction": "outbound", "elapsed_ms": 0,
        }
        logger.info("backend.publish_ignore.engine_query_request %s", fields)
        try:
            params = {**asdict(query), "request_id": engine_request_id}
            # COSEC: reuse the authorized binding connection, pinning each BaaS replica.
            ctx = await asyncio.to_thread(
                self.resolver.resolve_for_binding_invoke,
                binding.id, operator_id, bot_id=query.bot_id,
                device_uuid=target if binding.device_provider == "baas" else None,
            )
            if binding.device_provider == "baas":
                response = await self.transport.invoke(
                    ctx.conn_info, "GET", ENDPOINT, params=params, timeout=30,
                )
            else:
                # COSEC: URL/headers originate from the trusted resolver, not query input.
                raw = await asyncio.to_thread(
                    self.http.get, ctx.conn_info["url"].rstrip("/") + ENDPOINT,
                    params=params, headers=ctx.conn_info.get("headers", {}), timeout=30,
                )
                raw.raise_for_status()
                response = raw.json()
            if not isinstance(response, dict) or response.get("success") is not True:
                raise PublishIgnoreError("engine_rejected")
            data = response.get("data")
            if (
                not isinstance(data, dict)
                or not isinstance(data.get("paths"), list)
                or not all(isinstance(path, str) for path in data["paths"])
                or type(data.get("entry_count")) is not int
                or data["entry_count"] != len(data["paths"])
                or not isinstance(data.get("revision"), str)
                or not re.fullmatch(r"[0-9a-f]{64}", data["revision"])
            ):
                raise PublishIgnoreError("invalid_engine_response")
            result.update(status="success", paths=data["paths"],
                          entry_count=data["entry_count"], revision=data["revision"])
            log_result = dict(result)
            if sum(len(path.encode("utf-8")) for path in data["paths"]) > 4096:
                log_result.pop("paths")
                log_result["paths_omitted"] = True
            logger.info("backend.publish_ignore.engine_query_response %s", {
                **fields, **log_result, "elapsed_ms": int((time.monotonic() - started) * 1000),
            })
        except Exception as exc:
            unknown = isinstance(exc, (TimeoutError, DeviceAdapterTimeoutError, HttpClientTimeoutError))
            result.update(
                status="unknown" if unknown else "failed", error_type=type(exc).__name__,
                error_code=exc.code if isinstance(exc, PublishIgnoreError) else "engine_call_failed",
            )
            # COSEC: never include raw upstream exceptions, response bodies or credentials.
            logger.warning("backend.publish_ignore.engine_query_failure %s", {
                **fields, **result, "elapsed_ms": int((time.monotonic() - started) * 1000),
            })
        return result

    async def change(self, binding, target, command, operator_id):
        started = time.monotonic()
        result = {
            "provider": binding.device_provider,
            "binding_id": binding.id,
            "target_id": target,
        }
        engine_request_id = str(uuid4())
        fields = {
            **asdict(command),
            **result,
            "engine_request_id": engine_request_id,
            "operator_id": operator_id,
            "method": "POST",
            "route": ENDPOINT,
            "system": "engine",
            "direction": "outbound",
            "elapsed_ms": 0,
        }
        logger.info("backend.publish_ignore.engine_request %s", fields)
        try:
            payload = {
                "expected_target": {
                    k: getattr(command, k)
                    for k in ("bot_id", "entity_id", "stage")
                },
                "operation": command.operation,
                "path": command.path,
                "request_id": engine_request_id,
            }
            # COSEC: reuse the authenticated runtime connection after Bot management authorization.
            ctx = await asyncio.to_thread(
                self.resolver.resolve_for_binding_invoke,
                binding.id, operator_id, bot_id=command.bot_id,
                device_uuid=target if binding.device_provider == "baas" else None,
            )
            if binding.device_provider == "baas":
                response = await self.transport.invoke(
                    ctx.conn_info, "POST", ENDPOINT, body=payload, timeout=30
                )
            else:
                # COSEC: URL and credentials come only from the trusted binding resolver.
                url = ctx.conn_info["url"].rstrip("/") + ENDPOINT
                raw = await asyncio.to_thread(
                    self.http.post,
                    url,
                    json=payload,
                    headers=ctx.conn_info.get("headers", {}),
                    timeout=30,
                )
                raw.raise_for_status()
                response = raw.json()
            if response.get("success") is not True:
                raise PublishIgnoreError("engine_rejected")
            data = response["data"]
            if (
                type(data.get("changed")) is not bool
                or type(data.get("entry_count")) is not int
                or data["entry_count"] < 0
                or not isinstance(data.get("revision"), str)
                or not re.fullmatch(r"[0-9a-f]{64}", data["revision"])
            ):
                raise PublishIgnoreError("invalid_engine_response")
            result.update(
                status="changed" if data["changed"] else "unchanged",
                changed=data["changed"],
                entry_count=data["entry_count"],
                revision=data["revision"],
            )
            logger.info(
                "backend.publish_ignore.engine_response %s",
                {
                    **fields,
                    **result,
                    "elapsed_ms": int((time.monotonic() - started) * 1000),
                },
            )
        except Exception as exc:
            unknown = isinstance(
                exc, (TimeoutError, DeviceAdapterTimeoutError, HttpClientTimeoutError)
            )
            result.update(
                status="unknown" if unknown else "failed",
                error_type=type(exc).__name__,
                error_code=exc.code
                if isinstance(exc, PublishIgnoreError)
                else "engine_call_failed",
            )
            # COSEC: never log upstream exception text/body, which can contain reusable credentials.
            logger.warning(
                "backend.publish_ignore.engine_failure %s",
                {
                    **fields,
                    **result,
                    "elapsed_ms": int((time.monotonic() - started) * 1000),
                },
            )
        return result
