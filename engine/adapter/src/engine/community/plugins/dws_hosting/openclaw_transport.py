"""Dedicated hosting RPC socket; never uses or changes the interactive chat pool."""

import asyncio
import json
import logging
import math
import os
import stat
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, WebSocketException

from engine.community.kernel.dws_execution import ExecutionError, GatewayWitness
from engine.community.openclaw.client.gateway_connection import (
    _build_device_auth_payload_v3,
    _derive_device_id,
    _normalize_base64_url,
    _sign_payload,
)
from engine.community.openclaw.protocol import (
    ClientInfo,
    ConnectAuth,
    ConnectParams,
    DeviceIdentity,
)


@dataclass(frozen=True)
class HostingGatewayConfig:
    url: str
    device_key_file: Path
    token: str = field(repr=False)
    run_timeout: float = 300
    connection_timeout: float = 10

    def __post_init__(self):
        url = urlsplit(self.url)
        # COSEC: only trusted composition config; no insecure remote auth or URL credentials.
        if (
            url.scheme not in ("ws", "wss")
            or not url.hostname
            or url.username
            or url.password
            or url.query
            or url.fragment
            or (
                url.scheme == "ws"
                and url.hostname not in ("127.0.0.1", "::1", "localhost")
            )
        ):
            raise ValueError("invalid hosting gateway URL")
        if not self.device_key_file.is_absolute() or not self.token:
            raise ValueError("explicit device identity and token required")
        if any(
            isinstance(v, bool) or not math.isfinite(v) or v <= 0
            for v in (self.run_timeout, self.connection_timeout)
        ):
            raise ValueError("positive finite timeouts required")


class _NoRedirectConnect(connect):
    def process_redirect(self, exc):
        # COSEC: credentials must never follow an endpoint redirect.
        return exc


def _private_key(path: Path) -> Ed25519PrivateKey:
    # COSEC: read the existing identity only; never replace a corrupt/shared key.
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as source:
        info = os.fstat(source.fileno())
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_mode & 0o077
            or info.st_uid != os.getuid()
        ):
            raise ExecutionError("unsafe_device_identity")
        data = source.read(16_385)
    if len(data) > 16_384:
        raise ExecutionError("invalid_device_identity")
    key = serialization.load_pem_private_key(data, password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise ExecutionError("invalid_device_identity")
    return key


async def _frame(ws) -> dict:
    value = json.loads(await ws.recv())
    if not isinstance(value, dict):
        raise ExecutionError("invalid_gateway_frame")
    return value


class HostingGatewayTransport:
    def __init__(self, config: HostingGatewayConfig):
        self.config = config

    async def _handshake(self, ws):
        challenge = await _frame(ws)
        payload = challenge.get("payload")
        if (
            challenge.get("type") != "event"
            or challenge.get("event") != "connect.challenge"
            or not isinstance(payload, dict)
            or not isinstance(payload.get("nonce"), str)
            or not 1 <= len(payload["nonce"]) <= 1024
        ):
            raise ExecutionError("invalid_gateway_challenge")
        key = await asyncio.to_thread(_private_key, self.config.device_key_file)
        public = key.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        )
        device_id = _derive_device_id(public)
        signed_at = int(time.time() * 1000)
        scopes = ["operator.read", "operator.write"]
        signed = _build_device_auth_payload_v3(
            device_id,
            "gateway-client",
            "backend",
            "operator",
            scopes,
            signed_at,
            self.config.token,
            payload["nonce"],
            "python",
            None,
        )
        params = ConnectParams(
            min_protocol=3,
            max_protocol=4,
            client=ClientInfo(
                id="gateway-client", version="1.0.0", platform="python", mode="backend"
            ),
            role="operator",
            scopes=scopes,
            auth=ConnectAuth(token=self.config.token),
            device=DeviceIdentity(
                id=device_id,
                public_key=_normalize_base64_url(public),
                signature=_sign_payload(key, signed),
                signed_at=signed_at,
                nonce=payload["nonce"],
            ),
        )
        request_id = uuid4().hex
        before_ms = time.monotonic_ns() // 1_000_000
        await ws.send(
            json.dumps(
                {
                    "type": "req",
                    "id": request_id,
                    "method": "connect",
                    "params": params.to_dict(),
                }
            )
        )
        response = await _frame(ws)
        after_ms = time.monotonic_ns() // 1_000_000
        hello = response.get("payload")
        if (
            response.get("type") != "res"
            or response.get("id") != request_id
            or response.get("ok") is not True
            or not isinstance(hello, dict)
            or hello.get("type") != "hello-ok"
            or type(hello.get("protocol")) is not int
            or hello["protocol"] not in (3, 4)
        ):
            raise ExecutionError("gateway_auth_failed")
        return self._witness(hello, before_ms, after_ms)

    def _witness(self, hello, before_ms, after_ms):
        # COSEC: process uptime is comparable only for an authenticated loopback
        # gateway on the SAME host boot. Never infer a restart from wall-clock time.
        if urlsplit(self.config.url).hostname not in ("127.0.0.1", "::1", "localhost"):
            return None
        snapshot = hello.get("snapshot")
        uptime = snapshot.get("uptimeMs") if isinstance(snapshot, dict) else None
        if type(uptime) is not int or uptime < 0 or uptime > before_ms:
            return None
        try:
            boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
            if len(boot) != 36:
                return None
            return GatewayWitness(
                host_boot_id=boot,
                started_lower_ms=before_ms - uptime,
                started_upper_ms=after_ms - uptime,
            )
        except (OSError, ValueError):
            return None

    async def responses(
        self, rpc_id: str, method: str, params: dict, *, timeout: float
    ) -> AsyncIterator[dict]:
        if method not in ("agent", "agent.wait", "chat.abort"):
            raise ExecutionError("unsupported_hosting_rpc")
        if method == "chat.abort" and (
            set(params) != {"sessionKey", "runId"}
            or not isinstance(params["sessionKey"], str)
            or not isinstance(params["runId"], str)
            or ":dws:" not in params["sessionKey"]
            or not params["runId"].startswith("dws-turn-")
        ):
            raise ExecutionError("unsupported_hosting_rpc")
        # COSEC: WebSocket debug frame logs include tokens, private prompts and replies.
        logger = logging.Logger("dws.gateway.private", level=logging.CRITICAL + 1)
        logger.disabled = True
        logger.propagate = False
        deadline = asyncio.get_running_loop().time() + timeout

        def remaining():
            return max(0, deadline - asyncio.get_running_loop().time())

        try:
            async with _NoRedirectConnect(
                self.config.url,
                proxy=None,
                logger=logger,
                compression=None,
                # Match the existing OpenClaw client during long model turns.
                # The RPC deadline still bounds the full operation.
                ping_interval=60,
                ping_timeout=120,
                open_timeout=min(remaining(), self.config.connection_timeout),
                close_timeout=2,
                max_size=262_144,
                max_queue=16,
            ) as ws:
                witness = await asyncio.wait_for(
                    self._handshake(ws),
                    min(remaining(), self.config.connection_timeout),
                )
                if witness is not None:
                    # Consumer persists this before resuming the generator/send.
                    yield {"type": "hosting.gateway", "witness": witness.model_dump()}
                await asyncio.wait_for(
                    ws.send(
                        json.dumps(
                            {
                                "type": "req",
                                "id": rpc_id,
                                "method": method,
                                "params": params,
                            }
                        )
                    ),
                    remaining(),
                )
                while True:
                    response = await asyncio.wait_for(_frame(ws), remaining())
                    if response.get("type") == "res" and response.get("id") == rpc_id:
                        yield response
        except ConnectionClosed as exc:
            # COSEC: do not expose peer-supplied close reasons or frame contents.
            ping_timeout = (
                exc.sent is not None
                and exc.sent.code == 1011
                and exc.sent.reason == "keepalive ping timeout"
            )
            raise ExecutionError(
                "gateway_ping_timeout" if ping_timeout else "gateway_connection_closed"
            ) from None
        except TimeoutError:
            raise ExecutionError("gateway_result_timeout") from None
        except (OSError, WebSocketException, ValueError, TypeError, RecursionError):
            raise ExecutionError("gateway_result_unavailable") from None
