"""Publish-ignore transport using fixed BaaS replicas or an ARCA binding."""

import asyncio
import base64
import json
import re
import time
from dataclasses import asdict
from uuid import uuid4
from cryptography.hazmat.primitives.serialization import load_pem_private_key
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from agentclaw.community.kernel.publish_ignore import PublishIgnoreError
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.device_adapter_transport import (
    DeviceAdapterTimeoutError,
)
from agentclaw.community.plugin_api.http_client import HttpClientTimeoutError

logger = get_logger()
ENDPOINT = "/api/bot/publish-ignore"


class HttpPublishIgnoreRuntime:
    def __init__(self, baas, resolver, transport, http, secret: str):
        self.baas, self.resolver, self.transport, self.http = (
            baas,
            resolver,
            transport,
            http,
        )
        self.secret = secret

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
            if not self.secret:
                raise PublishIgnoreError("management_auth_not_configured")
            payload = {
                "expected_target": {
                    k: getattr(command, k)
                    for k in ("bot_id", "entity_id", "stage")
                },
                "operation": command.operation,
                "path": command.path,
                "request_id": engine_request_id,
            }
            timestamp = int(time.time())
            encoded = json.dumps(
                {**payload, "timestamp": timestamp},
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            )
            # COSEC: endpoint-specific signature is minted only after Bot management authorization.
            key = load_pem_private_key(self.secret.encode(), password=None)
            if not isinstance(key, Ed25519PrivateKey):
                raise PublishIgnoreError("invalid_management_signing_key")
            signature = base64.b64encode(key.sign(encoded.encode())).decode("ascii")
            payload["authorization"] = {"timestamp": timestamp, "signature": signature}
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
