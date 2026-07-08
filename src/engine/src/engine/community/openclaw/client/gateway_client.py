"""
OpenClaw Gateway WebSocket 客户端

连接 OpenClaw Gateway 的 WebSocket 客户端，支持：
- 连接管理（challenge 握手 + 设备签名认证）
- 请求/响应匹配（通过 request id）
- 事件订阅和分发
- 心跳保活（tick 事件）
- 设备身份签名（Ed25519）

与 MoltisGatewayClient 的关键差异：
- 握手：先接收 connect.challenge 事件，再发送 connect 请求带 token 和设备签名
- chat.send 参数：{ sessionKey, message, timeoutMs, idempotencyKey, attachments }
- 无 gateway_password
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import os
import ssl
import time
import uuid
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Dict, Optional

import websockets
from websockets.asyncio.client import ClientConnection

from engine.community.openclaw.protocol import (
    ClientInfo,
    ConnectAuth,
    ConnectParams,
    DeviceIdentity,
    ErrorCodes,
    EventFrame,
    HelloOk,
    OPENCLAW_GATEWAY_PROTOCOL_VERSION,
    RequestFrame,
    ResponseFrame,
)
from engine.community.openclaw.config import OpenClawConfig, get_config

log = logging.getLogger("openclaw-client")
_DEBUG = os.getenv("OPENCLAW_DEBUG_EVENTS", "").lower() in {"1", "true", "yes", "on"}
_DEFAULT_EARLY_FINAL_GRACE_SECONDS = 3.0


def _summarize_attachments(attachments: Any) -> dict[str, Any]:
    if attachments is None:
        return {"present": False, "count": 0}
    if not isinstance(attachments, list):
        return {"present": True, "valid": False, "type": type(attachments).__name__}

    items: list[dict[str, Any]] = []
    for index, item in enumerate(attachments):
        if not isinstance(item, dict):
            items.append({"index": index, "valid": False, "type": type(item).__name__})
            continue
        content = item.get("content")
        source = item.get("source")
        source_data = source.get("data") if isinstance(source, dict) else None
        items.append(
            {
                "index": index,
                "valid": True,
                "type": item.get("type"),
                "mimeType": item.get("mimeType") or item.get("media_type"),
                "fileName": item.get("fileName") or item.get("filename"),
                "contentLength": len(content) if isinstance(content, str) else None,
                "sourceType": source.get("type") if isinstance(source, dict) else None,
                "sourceMediaType": source.get("media_type") if isinstance(source, dict) else None,
                "sourceDataLength": len(source_data) if isinstance(source_data, str) else None,
            }
        )
    return {"present": True, "valid": True, "count": len(attachments), "items": items}


# ── 设备密钥管理 ──────────────────────────────────────────────────────────────

DEFAULT_DEVICE_KEY_FILE = Path.home() / ".openclaw" / "enterprise_device_key.pem"


def _get_early_final_grace_seconds() -> float:
    raw = (os.getenv("OPENCLAW_EARLY_FINAL_GRACE_SECONDS") or "").strip()
    if not raw:
        return _DEFAULT_EARLY_FINAL_GRACE_SECONDS
    try:
        value = float(raw)
    except ValueError:
        log.warning(
            "Invalid OPENCLAW_EARLY_FINAL_GRACE_SECONDS=%r, using %.1fs",
            raw,
            _DEFAULT_EARLY_FINAL_GRACE_SECONDS,
        )
        return _DEFAULT_EARLY_FINAL_GRACE_SECONDS
    return max(value, 0.0)


def _normalize_base64_url(data: bytes) -> str:
    """将字节转换为 Base64URL 编码（无填充）"""
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _derive_device_id(public_key_bytes: bytes) -> str:
    """从公钥派生设备 ID（SHA-256 完整 hexdigest，64 字符）"""
    return hashlib.sha256(public_key_bytes).hexdigest()


def _build_device_auth_payload_v3(
    device_id: str,
    client_id: str,
    client_mode: str,
    role: str,
    scopes: list[str],
    signed_at_ms: int,
    token: Optional[str],
    nonce: str,
    platform: Optional[str],
    device_family: Optional[str],
) -> str:
    """构建 v3 签名 payload

    格式: v3|deviceId|clientId|clientMode|role|scopes|signedAt|token|nonce|platform|deviceFamily
    """
    scopes_str = ",".join(scopes)
    token_str = token or ""
    platform_str = platform or ""
    device_family_str = device_family or ""
    return "|".join([
        "v3",
        device_id,
        client_id,
        client_mode,
        role,
        scopes_str,
        str(signed_at_ms),
        token_str,
        nonce,
        platform_str,
        device_family_str,
    ])


def _load_or_create_device_keypair(
    key_file: Path = DEFAULT_DEVICE_KEY_FILE,
) -> tuple[Any, str, str]:
    """加载或创建设备密钥对

    返回: (private_key, public_key_base64url, device_id)
    """
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        from cryptography.hazmat.primitives import serialization
    except ImportError:
        raise ImportError(
            "设备签名需要 'cryptography' 库。请安装: pip install cryptography"
        )

    key_file.parent.mkdir(parents=True, exist_ok=True)

    if key_file.exists():
        try:
            with open(key_file, "rb") as f:
                private_key = serialization.load_pem_private_key(f.read(), password=None)
            if not isinstance(private_key, Ed25519PrivateKey):
                raise ValueError("Invalid key type")
            pub_bytes = private_key.public_key().public_bytes(
                encoding=serialization.Encoding.Raw,
                format=serialization.PublicFormat.Raw,
            )
            public_key_b64 = _normalize_base64_url(pub_bytes)
            device_id = _derive_device_id(pub_bytes)
            log.info(f"[device] 已加载设备密钥: device_id={device_id}")
            return private_key, public_key_b64, device_id
        except Exception as e:
            log.warning(f"[device] 加载密钥失败，将重新生成: {e}")

    # 生成新密钥对
    private_key = Ed25519PrivateKey.generate()
    pub_bytes = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    public_key_b64 = _normalize_base64_url(pub_bytes)
    device_id = _derive_device_id(pub_bytes)

    # 保存私钥
    pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    key_file.write_bytes(pem)
    key_file.chmod(0o600)  # 仅所有者可读写
    log.info(f"[device] 已生成并保存设备密钥: device_id={device_id}, path={key_file}")

    return private_key, public_key_b64, device_id


def _sign_payload(private_key: Any, payload: str) -> str:
    """签名 payload，返回 Base64URL 编码的签名"""
    signature = private_key.sign(payload.encode("utf-8"))
    return _normalize_base64_url(signature)


class OpenClawGatewayClient:
    """OpenClaw Gateway WebSocket 客户端"""

    def __init__(
        self,
        gateway_url: Optional[str] = None,
        gateway_token: Optional[str] = None,
        config: Optional[OpenClawConfig] = None,
        device_key_file: Optional[Path] = None,
        upstream_headers: Optional[Dict[str, str]] = None,
    ):
        if config:
            self._config = config
        else:
            self._config = get_config()
            if gateway_url:
                self._config.gateway_url = gateway_url
            if gateway_token:
                self._config.gateway_token = gateway_token

        self._ws: Optional[ClientConnection] = None
        self._hello: Optional[HelloOk] = None
        self._connected = False
        self._closing = False
        # 统一透传 header（由 server 按配置开关决定是否传入）
        self._upstream_headers = dict(upstream_headers or {})

        # 请求响应匹配
        self._pending_requests: Dict[str, asyncio.Future] = {}

        # 事件监听器
        self._event_listeners: Dict[str, list[Callable[[EventFrame], None]]] = {}

        # 消息接收任务
        self._recv_task: Optional[asyncio.Task] = None

        # 序列号
        self._seq = 0

        # 设备密钥（延迟加载）
        self._device_key_file = device_key_file or DEFAULT_DEVICE_KEY_FILE
        self._device_private_key: Optional[Any] = None
        self._device_public_key_b64: Optional[str] = None
        self._device_id: Optional[str] = None

    @property
    def connected(self) -> bool:
        return self._connected and self._ws is not None

    @property
    def hello(self) -> Optional[HelloOk]:
        return self._hello

    def _next_id(self) -> str:
        return uuid.uuid4().hex[:8]

    def _next_seq(self) -> int:
        self._seq += 1
        return self._seq

    async def connect(self) -> HelloOk:
        """连接到 OpenClaw Gateway 并完成握手（challenge 模式 + 设备签名）"""
        if self._connected:
            if self._hello:
                return self._hello
            raise RuntimeError("Already connecting")

        log.info(f"Connecting to OpenClaw Gateway: {self._config.gateway_url}")

        try:
            ssl_context = None
            if self._config.gateway_url.startswith("wss://"):
                ssl_context = ssl.create_default_context()
                ssl_context.check_hostname = False
                ssl_context.verify_mode = ssl.CERT_NONE

            connect_kwargs: Dict[str, Any] = {
                "max_size": 8 * 1024 * 1024,
                "ssl": ssl_context,
                "ping_interval": 60,
                "ping_timeout": 120,
            }
            if self._upstream_headers:
                # 在连接握手阶段附加自定义 header
                connect_kwargs["additional_headers"] = self._upstream_headers
            self._ws = await asyncio.wait_for(
                websockets.connect(self._config.gateway_url, **connect_kwargs),
                timeout=self._config.connection_timeout,
            )
        except asyncio.TimeoutError:
            raise ConnectionError(f"Connection timeout: {self._config.gateway_url}")
        except Exception as e:
            raise ConnectionError(f"Failed to connect: {e}")

        # OpenClaw 握手：接收 connect.challenge 事件，获取 nonce
        nonce = None
        try:
            raw = await asyncio.wait_for(
                self._ws.recv(),
                timeout=self._config.connection_timeout,
            )
            challenge_data = json.loads(raw)
            if challenge_data.get("type") == "event" and challenge_data.get("event") == "connect.challenge":
                payload = challenge_data.get("payload", {})
                nonce = payload.get("nonce", "")
                log.debug(f"Received connect.challenge, nonce={nonce[:8]}...")
            else:
                log.debug(f"Unexpected first frame (not challenge): {challenge_data.get('type')}/{challenge_data.get('event')}, proceeding anyway")
        except asyncio.TimeoutError:
            await self._ws.close()
            self._ws = None
            raise ConnectionError("Challenge timeout")

        # 加载或创建设备密钥对
        if self._device_private_key is None:
            try:
                self._device_private_key, self._device_public_key_b64, self._device_id = \
                    _load_or_create_device_keypair(self._device_key_file)
            except ImportError as e:
                log.warning(f"[device] 无法加载设备密钥，将使用无设备身份模式连接: {e}")

        # 发送 connect 请求（带设备签名）
        auth = None
        if self._config.gateway_token:
            auth = ConnectAuth(token=self._config.gateway_token)

        scopes = ["operator.read", "operator.write", "operator.admin"]

        # 构建设备签名（如果有设备密钥）
        device_identity: Optional[DeviceIdentity] = None
        if self._device_private_key and self._device_public_key_b64 and self._device_id and nonce:
            signed_at = int(time.time() * 1000)
            payload = _build_device_auth_payload_v3(
                device_id=self._device_id,
                client_id="gateway-client",
                client_mode="backend",
                role="operator",
                scopes=scopes,
                signed_at_ms=signed_at,
                token=self._config.gateway_token,
                nonce=nonce,
                platform="python",
                device_family=None,
            )
            signature = _sign_payload(self._device_private_key, payload)
            device_identity = DeviceIdentity(
                id=self._device_id,
                public_key=self._device_public_key_b64,
                signature=signature,
                signed_at=signed_at,
                nonce=nonce,
            )
            log.info(f"[connect] 已生成设备签名: device_id={self._device_id}")

        connect_params = ConnectParams(
            min_protocol=OPENCLAW_GATEWAY_PROTOCOL_VERSION,
            max_protocol=OPENCLAW_GATEWAY_PROTOCOL_VERSION,
            client=ClientInfo(
                id="gateway-client",
                version="1.0.0",
                platform="python",
                mode="backend",
            ),
            # tool-events cap 让 gateway 推送 stream:"tool" 事件
            caps=["tool-events"],
            role="operator",
            # operator.read: 读权限, operator.write: 写权限, operator.admin: approvals 权限
            scopes=scopes,
            auth=auth,
            device=device_identity,
        )

        request_id = self._next_id()
        request = RequestFrame(
            id=request_id,
            method="connect",
            params=connect_params.to_dict(),
        )

        log.info(f"[connect] 发送连接请求: id={request_id}, scopes={scopes}, has_token={auth is not None}, has_device={device_identity is not None}")
        log.debug(f"[connect] 请求参数: {connect_params.to_dict()}")

        await self._ws.send(request.to_json())

        # 等待响应
        try:
            raw = await asyncio.wait_for(
                self._ws.recv(),
                timeout=self._config.connection_timeout,
            )
        except asyncio.TimeoutError:
            await self._ws.close()
            self._ws = None
            raise ConnectionError("Handshake timeout")

        response = ResponseFrame.from_dict(json.loads(raw))
        log.info(f"[connect] 收到响应: ok={response.ok}, error={response.error}")

        if not response.ok:
            await self._ws.close()
            self._ws = None
            error_msg = response.error.message if response.error else "Unknown error"
            error_details = response.error.details if response.error else None
            # 如果是设备身份相关错误，给出提示
            if error_details and isinstance(error_details, dict):
                detail_code = error_details.get("code", "")
                if detail_code.startswith("DEVICE_AUTH_"):
                    log.error(f"[connect] 设备认证失败: code={detail_code}, reason={error_details.get('reason')}")
            raise ConnectionError(f"Handshake failed: {error_msg}")

        self._hello = HelloOk.from_dict(response.payload)
        self._connected = True

        # 打印服务端返回的详细信息
        log.info(f"Connected to OpenClaw Gateway: protocol={self._hello.protocol}, conn_id={self._hello.server.conn_id}")
        log.info(f"[connect] 服务端响应 payload: {response.payload}")
        if self._hello.auth:
            log.info(f"[connect] 服务端授权信息: role={self._hello.auth.role}, scopes={self._hello.auth.scopes}")

        # 启动消息接收循环
        self._recv_task = asyncio.create_task(self._recv_loop())

        return self._hello

    async def disconnect(self) -> None:
        """断开连接"""
        self._closing = True
        self._connected = False

        if self._recv_task:
            self._recv_task.cancel()
            try:
                await self._recv_task
            except asyncio.CancelledError:
                pass
            self._recv_task = None

        if self._ws:
            await self._ws.close()
            self._ws = None

        for future in self._pending_requests.values():
            if not future.done():
                future.set_exception(ConnectionError("Connection closed"))
        self._pending_requests.clear()

        self._hello = None
        self._closing = False
        log.info("Disconnected from OpenClaw Gateway")

    # ── 消息接收 ────────────────────────────────────────────────────────────

    async def _recv_loop(self) -> None:
        """消息接收循环"""
        if not self._ws:
            return

        try:
            async for raw in self._ws:
                if self._closing:
                    break

                try:
                    data = json.loads(raw)
                    frame_type = data.get("type")

                    if frame_type == "res":
                        response = ResponseFrame.from_dict(data)
                        future = self._pending_requests.pop(response.id, None)
                        if future and not future.done():
                            future.set_result(response)

                    elif frame_type == "event":
                        event = EventFrame.from_dict(data)
                        await self._dispatch_event(event)

                except json.JSONDecodeError as e:
                    log.warning(f"Invalid JSON frame: {e}")
                except Exception as e:
                    log.exception(f"Error processing frame: {e}")

        except websockets.ConnectionClosed:
            log.info("WebSocket connection closed")
        except Exception as e:
            log.exception(f"Recv loop error: {e}")
        finally:
            self._connected = False

    async def _dispatch_event(self, event: EventFrame) -> None:
        """分发事件到监听器"""
        if _DEBUG:
            payload = event.payload if isinstance(event.payload, dict) else {}
            log.info(
                "[debug][client.recv] event=%s runId=%s sessionKey=%s keys=%s",
                event.event,
                payload.get("runId"),
                payload.get("sessionKey"),
                sorted(payload.keys()) if payload else [],
            )

        listeners = self._event_listeners.get(event.event, [])
        for listener in listeners:
            try:
                result = listener(event)
                if asyncio.iscoroutine(result):
                    await result
            except Exception as e:
                log.exception(f"Event listener error: {e}")

        # 通配符监听器
        wildcard_listeners = self._event_listeners.get("*", [])
        for listener in wildcard_listeners:
            try:
                result = listener(event)
                if asyncio.iscoroutine(result):
                    await result
            except Exception as e:
                log.exception(f"Wildcard event listener error: {e}")

    def on_event(self, event_name: str, listener: Callable[[EventFrame], None]) -> None:
        """注册事件监听器"""
        if event_name not in self._event_listeners:
            self._event_listeners[event_name] = []
        self._event_listeners[event_name].append(listener)

    def off_event(self, event_name: str, listener: Callable[[EventFrame], None]) -> None:
        """移除事件监听器"""
        if event_name in self._event_listeners:
            try:
                self._event_listeners[event_name].remove(listener)
            except ValueError:
                pass

    # ── RPC 请求 ────────────────────────────────────────────────────────────

    async def send_request(
        self,
        method: str,
        params: Optional[Dict[str, Any]] = None,
        timeout: float = 30.0,
    ) -> ResponseFrame:
        """发送 RPC 请求并等待响应"""
        request_id = self._next_id()
        return await self.send_request_with_id(request_id, method, params, timeout)

    async def send_request_with_id(
        self,
        request_id: str,
        method: str,
        params: Optional[Dict[str, Any]] = None,
        timeout: float = 30.0,
    ) -> ResponseFrame:
        """发送 RPC 请求并等待响应（保留调用方提供的 request id）"""
        if not self._connected or not self._ws:
            raise ConnectionError("Not connected")

        if request_id in self._pending_requests:
            raise ValueError(f"Request id already pending: {request_id}")

        request = RequestFrame(id=request_id, method=method, params=params)

        future: asyncio.Future[ResponseFrame] = asyncio.get_event_loop().create_future()
        self._pending_requests[request_id] = future

        try:
            if method == "chat.send":
                request_params = params or {}
                log.info(
                    "[attachments][gateway_send_request] requestId=%s hasAttachments=%s "
                    "paramKeys=%s summary=%s",
                    request_id,
                    "attachments" in request_params,
                    sorted(request_params.keys()),
                    _summarize_attachments(request_params.get("attachments")),
                )
            await self._ws.send(request.to_json())
            log.debug(f"Sent request: method={method}, id={request_id}")

            response = await asyncio.wait_for(future, timeout=timeout)
            return response

        except asyncio.TimeoutError:
            self._pending_requests.pop(request_id, None)
            raise TimeoutError(f"Request timeout: {method}")
        except Exception:
            self._pending_requests.pop(request_id, None)
            raise

    async def send_raw_frame(self, frame: Dict[str, Any]) -> None:
        """按原样转发任意帧到 OpenClaw Gateway"""
        if not self._connected or not self._ws:
            raise ConnectionError("Not connected")

        await self._ws.send(json.dumps(frame))
        log.debug("Forwarded raw frame: type=%s", frame.get("type"))

    # ── 聊天 ────────────────────────────────────────────────────────────────

    async def chat_stream(
        self,
        session_key: str,
        message: str,
        timeout_ms: Optional[int] = None,
        idempotency_key: Optional[str] = None,
        attachments: Optional[list[dict[str, Any]]] = None,
    ) -> AsyncIterator[Dict[str, Any]]:
        """
        发送聊天消息并流式返回事件

        OpenClaw chat.send 参数: { sessionKey, message, timeoutMs, idempotencyKey, attachments }
        """
        if not self._connected:
            raise ConnectionError("Not connected")

        event_queue: asyncio.Queue[Optional[EventFrame]] = asyncio.Queue()
        done = False
        expected_run_id = idempotency_key or uuid.uuid4().hex
        run_id: Optional[str] = expected_run_id
        allowed_run_ids: set[str] = {expected_run_id}
        pending_early_final: Optional[EventFrame] = None
        emitted_non_terminal = False
        early_final_grace_seconds = _get_early_final_grace_seconds()
        filter_drop_count = 0
        # Track pending subagent announce phases.  Incremented when a
        # sessions_spawn tool completes successfully; decremented when
        # the corresponding announce-run's chat-final arrives.
        pending_announces = 0

        def event_summary(event: EventFrame, payload: Dict[str, Any]) -> Dict[str, Any]:
            data = payload.get("data")
            message = payload.get("message")
            return {
                "event": event.event,
                "state": payload.get("state"),
                "stream": payload.get("stream"),
                "runId": payload.get("runId"),
                "sessionKey": payload.get("sessionKey"),
                "seq": payload.get("seq"),
                "phase": data.get("phase") if isinstance(data, dict) else None,
                "name": data.get("name") if isinstance(data, dict) else None,
                "hasMessage": isinstance(message, dict),
                "hasText": bool(payload.get("text")),
                "expectedRunId": expected_run_id,
                "currentRunId": run_id,
                "pendingAnnounces": pending_announces,
            }

        def is_empty_final_payload(payload: Dict[str, Any]) -> bool:
            if payload.get("state") != "final":
                return False
            if payload.get("text"):
                return False
            message = payload.get("message")
            if isinstance(message, dict):
                content = message.get("content")
                if isinstance(content, list) and content:
                    return False
                if message.get("text"):
                    return False
            return True

        def matches_current_stream(payload: Dict[str, Any]) -> bool:
            payload_run_id = payload.get("runId")
            if isinstance(payload_run_id, str) and payload_run_id:
                if payload_run_id in allowed_run_ids or payload_run_id == run_id:
                    return True
                if payload_run_id.startswith("inject-"):
                    return True
                if payload_run_id.startswith("announce:") and pending_announces > 0:
                    return True
                if pending_early_final is not None and payload.get("state") not in ("final", "error", "aborted"):
                    payload_session_key = payload.get("sessionKey")
                    return payload_session_key == session_key
                return False

            payload_session_key = payload.get("sessionKey")
            if payload_session_key:
                return payload_session_key == session_key
            return True

        def on_chat_event(event: EventFrame) -> None:
            nonlocal done, pending_announces, pending_early_final, emitted_non_terminal, filter_drop_count
            if done:
                return
            payload = event.payload or {}
            if not matches_current_stream(payload):
                filter_drop_count += 1
                log.info("[chat_stream][filter_drop] %s", event_summary(event, payload))
                return

            # Detect successful sessions_spawn tool result → expect an
            # announce phase later for each spawned subagent.
            data = payload.get("data") or {}
            stream_val = payload.get("stream", "")
            if (
                stream_val == "tool"
                and isinstance(data, dict)
                and data.get("name") == "sessions_spawn"
                and data.get("phase") == "result"
                and not data.get("isError")
            ):
                pending_announces += 1
                log.debug(
                    "[chat_stream] sessions_spawn succeeded, pending_announces=%d",
                    pending_announces,
                )

            state = payload.get("state")
            payload_run_id = payload.get("runId")
            if (
                pending_early_final is not None
                and state not in ("final", "error", "aborted")
                and isinstance(payload_run_id, str)
                and payload_run_id
            ):
                allowed_run_ids.add(payload_run_id)
                log.info(
                    "[chat_stream][early_final_followup_run] runId=%s allowedRunIds=%s",
                    payload_run_id,
                    sorted(allowed_run_ids),
                )
            if state in ("final", "error", "aborted"):
                log.info("[chat_stream][terminal_recv] %s", event_summary(event, payload))

            if state in ("error", "aborted"):
                # Hard stop — always terminate regardless of pending announces.
                payload["_source_event"] = event.event
                event_queue.put_nowait(event)
                done = True
                event_queue.put_nowait(None)

            elif state == "final":
                payload_run_id = payload.get("runId", "")
                is_inject = isinstance(payload_run_id, str) and payload_run_id.startswith("inject-")
                if is_inject:
                    # chat.inject broadcasts carry state="final" but are
                    # out-of-band injected messages, not the end of the
                    # current chat stream — forward without terminating.
                    payload["_source_event"] = event.event
                    event_queue.put_nowait(event)
                elif pending_announces > 0:
                    is_announce = isinstance(payload_run_id, str) and payload_run_id.startswith("announce:")
                    if is_announce:
                        pending_announces -= 1
                    if is_announce and pending_announces <= 0:
                        # Last announce-run completed — forward and terminate.
                        payload["_source_event"] = event.event
                        event_queue.put_nowait(event)
                        done = True
                        event_queue.put_nowait(None)
                    else:
                        # Either a Phase-1 chat-final or an intermediate
                        # announce chat-final — suppress so the yield loop
                        # (which breaks on any state="final") doesn't exit
                        # prematurely.
                        log.debug(
                            "[chat_stream] Suppressing intermediate chat-final "
                            "(pending_announces=%d, runId=%s)",
                            pending_announces,
                            payload.get("runId", "")[:40],
                        )
                else:
                    # No pending subagents — normal completion.
                    payload["_source_event"] = event.event
                    if (
                        not emitted_non_terminal
                        and early_final_grace_seconds > 0
                        and is_empty_final_payload(payload)
                    ):
                        pending_early_final = event
                        event_queue.put_nowait(event)
                        log.info(
                            "[chat_stream][early_final_defer_done] %s",
                            event_summary(event, payload),
                        )
                        return
                    event_queue.put_nowait(event)
                    done = True
                    event_queue.put_nowait(None)

            else:
                # All other events (lifecycle, assistant, tool, delta, etc.)
                payload["_source_event"] = event.event
                event_queue.put_nowait(event)

        def on_approval_requested(event: EventFrame) -> None:
            if done:
                return
            payload = dict(event.payload or {})
            payload.setdefault("approvalId", payload.get("requestId") or payload.get("id"))
            payload.setdefault("runId", run_id)
            payload.setdefault("sessionKey", session_key)
            if not matches_current_stream(payload):
                return
            payload.setdefault("state", "approval_requested")
            payload["event"] = event.event
            payload["_source_event"] = event.event
            event_queue.put_nowait(EventFrame(event="chat", payload=payload))

        def on_approval_resolved(event: EventFrame) -> None:
            if done:
                return
            payload = dict(event.payload or {})
            payload.setdefault("approvalId", payload.get("requestId") or payload.get("id"))
            payload.setdefault("runId", run_id)
            payload.setdefault("sessionKey", session_key)
            if not matches_current_stream(payload):
                return
            payload.setdefault("state", "approval_resolved")
            payload["event"] = event.event
            payload["_source_event"] = event.event
            event_queue.put_nowait(EventFrame(event="chat", payload=payload))

        # 注册事件监听
        self.on_event("chat", on_chat_event)
        self.on_event("agent", on_chat_event)
        self.on_event("approval.requested", on_approval_requested)
        self.on_event("approval.resolved", on_approval_resolved)
        self.on_event("exec.approval.requested", on_approval_requested)
        self.on_event("exec.approval.resolved", on_approval_resolved)

        try:
            # OpenClaw chat.send 参数
            params: Dict[str, Any] = {
                "sessionKey": session_key,
                "message": message,
                "idempotencyKey": expected_run_id,
            }
            if timeout_ms:
                params["timeoutMs"] = timeout_ms
            if attachments is not None:
                params["attachments"] = attachments
                log.info(
                    "[attachments][gateway_chat_stream] sessionKey=%s expectedRunId=%s summary=%s",
                    session_key,
                    expected_run_id,
                    _summarize_attachments(attachments),
                )

            log.info(
                "[chat_stream][send] sessionKey=%s expectedRunId=%s timeoutMs=%s "
                "providedIdempotencyKey=%s",
                session_key,
                expected_run_id,
                timeout_ms,
                bool(idempotency_key),
            )
            response = await self.send_request("chat.send", params)
            if not response.ok:
                error_msg = response.error.message if response.error else "Unknown error"
                log.info(
                    "[chat_stream][send_response] ok=false sessionKey=%s expectedRunId=%s "
                    "errorCode=%s errorMessage=%s",
                    session_key,
                    expected_run_id,
                    response.error.code if response.error else "UNKNOWN",
                    error_msg,
                )
                yield {
                    "state": "error",
                    "errorMessage": error_msg,
                    "errorCode": response.error.code if response.error else "UNKNOWN",
                }
                return

            resp_payload = response.payload or {}
            run_id = resp_payload.get("runId")
            if isinstance(run_id, str) and run_id:
                allowed_run_ids.add(run_id)
            log.info(
                "[chat_stream][send_response] ok=true sessionKey=%s expectedRunId=%s "
                "responseRunId=%s payloadKeys=%s",
                session_key,
                expected_run_id,
                run_id,
                sorted(resp_payload.keys()) if isinstance(resp_payload, dict) else [],
            )
            if not run_id:
                log.warning("chat.send response missing runId, falling back to unfiltered mode")

            timeout = (timeout_ms / 1000) if timeout_ms else 300
            while True:
                try:
                    wait_timeout = (
                        early_final_grace_seconds
                        if pending_early_final is not None
                        else timeout
                    )
                    event = await asyncio.wait_for(event_queue.get(), timeout=wait_timeout)
                    if event is None:
                        if pending_early_final is not None:
                            payload = pending_early_final.payload or {}
                            log.info(
                                "[chat_stream][early_final_flush_on_done] %s",
                                event_summary(pending_early_final, payload),
                            )
                            yield payload
                        break

                    payload = event.payload or {}
                    state = payload.get("state")
                    is_terminal = state in ("final", "error", "aborted")
                    is_inject_terminal = (
                        is_terminal
                        and isinstance(payload.get("runId"), str)
                        and payload.get("runId", "").startswith("inject-")
                    )

                    if (
                        is_empty_final_payload(payload)
                        and not emitted_non_terminal
                        and not is_inject_terminal
                        and early_final_grace_seconds > 0
                    ):
                        pending_early_final = event
                        log.info(
                            "[chat_stream][early_final_hold] graceSeconds=%.3f %s",
                            early_final_grace_seconds,
                            event_summary(event, payload),
                        )
                        continue

                    if pending_early_final is not None and not is_terminal:
                        log.info(
                            "[chat_stream][early_final_discard] heldFinal=%s firstFollowup=%s",
                            event_summary(pending_early_final, pending_early_final.payload or {}),
                            event_summary(event, payload),
                        )
                        pending_early_final = None

                    log.info("[chat_stream][yield] %s", event_summary(event, payload))
                    yield payload

                    if not is_terminal:
                        emitted_non_terminal = True
                    # 只有 chat 事件的 final/error/aborted 状态才结束流
                    # lifecycle/end 只表示 agent 运行结束，但不代表 chat 事件已结束
                    # chat.inject 的 runId 以 "inject-" 开头，不应终止流
                    if is_terminal:
                        payload_run_id = payload.get("runId", "")
                        if isinstance(payload_run_id, str) and payload_run_id.startswith("inject-"):
                            continue
                        break

                except asyncio.TimeoutError:
                    if pending_early_final is not None:
                        payload = pending_early_final.payload or {}
                        log.info(
                            "[chat_stream][early_final_flush_on_timeout] %s",
                            event_summary(pending_early_final, payload),
                        )
                        yield payload
                        break

                    # 诊断：推断 timeout 最可能的原因
                    if not self._connected or self._ws is None:
                        reason = "ws_disconnected"
                    elif not emitted_non_terminal and filter_drop_count == 0:
                        reason = "no_event_received_from_upstream"
                    elif not emitted_non_terminal and filter_drop_count > 0:
                        reason = f"all_events_filtered_drop_count={filter_drop_count}"
                    elif pending_announces > 0:
                        reason = f"waiting_subagent_final_pending_announces={pending_announces}"
                    else:
                        reason = "stream_stalled_mid_response"

                    log.warning(
                        "[chat_stream][timeout] sessionKey=%s expectedRunId=%s "
                        "currentRunId=%s timeoutSeconds=%.1f reason=%s "
                        "emittedNonTerminal=%s filterDropCount=%d pendingAnnounces=%d "
                        "allowedRunIds=%s wsConnected=%s",
                        session_key,
                        expected_run_id,
                        run_id,
                        timeout,
                        reason,
                        emitted_non_terminal,
                        filter_drop_count,
                        pending_announces,
                        sorted(allowed_run_ids),
                        self._connected,
                    )

                    yield {
                        "state": "error",
                        "errorMessage": "Chat stream timeout",
                        "errorCode": ErrorCodes.AGENT_TIMEOUT,
                    }
                    break

        finally:
            done = True
            self.off_event("chat", on_chat_event)
            self.off_event("agent", on_chat_event)
            self.off_event("approval.requested", on_approval_requested)
            self.off_event("approval.resolved", on_approval_resolved)
            self.off_event("exec.approval.requested", on_approval_requested)
            self.off_event("exec.approval.resolved", on_approval_resolved)

    async def chat_abort(
        self,
        session_key: str,
        run_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """中止聊天会话"""
        params: Dict[str, Any] = {"sessionKey": session_key}
        if run_id:
            params["runId"] = run_id

        response = await self.send_request("chat.abort", params)
        if response.ok:
            return {"success": True, "payload": response.payload}
        else:
            return {
                "success": False,
                "error": response.error.to_dict() if response.error else {"code": "UNKNOWN", "message": "Unknown error"},
            }

    async def approval_resolve(
        self,
        approval_id: str,
        approved: bool,
        comment: Optional[str] = None,
        command: Optional[str] = None,
    ) -> Dict[str, Any]:
        """提交审批结果"""
        params: Dict[str, Any] = {
            "approvalId": approval_id,
            "requestId": approval_id,
            "approved": approved,
            "decision": "approved" if approved else "denied",
        }
        if comment is not None:
            params["comment"] = comment
        if command is not None:
            params["command"] = command

        response = await self.send_request("exec.approval.resolve", params)
        if response.ok:
            return {"success": True, "payload": response.payload}
        else:
            return {
                "success": False,
                "error": response.error.to_dict() if response.error else {"code": "UNKNOWN", "message": "Unknown error"},
            }

    async def session_reset(self, session_key: str) -> Dict[str, Any]:
        """重置会话"""
        response = await self.send_request("sessions.reset", {"key": session_key})
        if response.ok:
            return {"success": True, "payload": response.payload}
        else:
            return {
                "success": False,
                "error": response.error.to_dict() if response.error else {"code": "UNKNOWN", "message": "Unknown error"},
            }


# ── 连接池 ──────────────────────────────────────────────────────────────────


class ConnectionPool:
    """OpenClaw Gateway WS 连接池。

    管理 N 条 OpenClawGatewayClient，round-robin 分配。
    每条连接独立维护 _recv_loop 和 event_listeners。
    ws_pool_size=1 时行为等同旧 singleton 模式。
    """

    def __init__(self, size: int = 3, config: Optional["OpenClawConfig"] = None):
        self._size = max(1, size)
        self._config = config
        self._clients: list[OpenClawGatewayClient] = []
        self._lock: Optional[asyncio.Lock] = None
        self._index = 0
        self._initialized = False

    def _get_lock(self) -> asyncio.Lock:
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    async def _ensure_initialized(self) -> None:
        if self._initialized:
            return
        async with self._get_lock():
            if self._initialized:
                return
            for i in range(self._size):
                client = OpenClawGatewayClient(config=self._config)
                self._clients.append(client)
            self._initialized = True
            log.info("[pool] initialized %d slots", self._size)

    async def get(self) -> OpenClawGatewayClient:
        """获取一条可用连接（round-robin + 自动重连）。"""
        await self._ensure_initialized()
        for _ in range(self._size):
            idx = self._index % self._size
            self._index += 1
            client = self._clients[idx]
            if client.connected:
                return client
            try:
                await client.connect()
                return client
            except Exception as e:
                log.warning("[pool] connect failed for slot %d: %s", idx, e)
                if client._ws:
                    try:
                        await client._ws.close()
                    except Exception:
                        pass
                    client._ws = None
                continue
        raise ConnectionError("All pool connections failed")

    async def get_least_busy(self) -> OpenClawGatewayClient:
        """获取 pending_requests 最少的连接。"""
        await self._ensure_initialized()
        connected = [c for c in self._clients if c.connected]
        if not connected:
            return await self.get()
        return min(connected, key=lambda c: len(c._pending_requests))

    async def shutdown(self) -> None:
        """关闭所有连接。"""
        for client in self._clients:
            try:
                await client.disconnect()
            except Exception as e:
                log.warning("[pool] disconnect failed: %s", e)
        self._clients.clear()
        self._initialized = False
        log.info("[pool] shutdown complete")


# ── 模块级 API（兼容旧接口） ──────────────────────────────────────────────

_pool: Optional[ConnectionPool] = None


def _get_pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        from engine.community.openclaw.config import get_config
        cfg = get_config()
        _pool = ConnectionPool(size=cfg.ws_pool_size, config=cfg)
    return _pool


def get_shared_client() -> OpenClawGatewayClient:
    """Return the first pool slot synchronously (construction-time use, no connect).

    Plugins typically hold this instance and connect lazily via their own
    `_ensure_connected()` on first use.
    """
    pool = _get_pool()
    if not pool._clients:
        pool._clients.append(OpenClawGatewayClient(config=pool._config))
        pool._initialized = True
    return pool._clients[0]


async def get_client() -> OpenClawGatewayClient:
    """获取一条可用连接（round-robin，兼容旧接口）。"""
    return await _get_pool().get()


async def close_client() -> None:
    """关闭连接池。"""
    global _pool
    if _pool:
        await _pool.shutdown()
        _pool = None
