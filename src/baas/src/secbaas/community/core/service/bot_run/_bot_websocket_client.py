"""
OpenClaw WebSocket 客户端（纯异步版本）

基于 websockets 异步库实现的客户端，支持：
- 握手：发送 connect 请求，接收 hello-ok 响应
- 消息循环：发送 RequestFrame，接收 ResponseFrame 和 EventFrame
- 聊天：发送 chat.send 请求，接收流式事件

全部基于 asyncio，不使用任何 threading，不会阻塞 event loop。
"""

import asyncio
import json
import os
import ssl
import uuid
from collections.abc import Callable
from typing import Any

import websockets
from websockets.asyncio.client import ClientConnection

from secbaas.community.core.utils.env_utils import is_dev
from secbaas.community.logger import get_logger

logger = get_logger("core-bot-run")

# 事件处理器类型：同步或异步均可，不关心返回值
EventHandler = Callable[..., Any]


class ChatRequestError(Exception):
    """chat.send / chat.inject 响应 ok=False 时抛出。

    当服务器返回的响应中 ok 字段为 False 时（例如会话验证失败、服务不可用），
    BotWebSocketClient.chat_send / chat_inject 会抛出此异常，避免静默失败。

    Attributes:
        error_code: 服务器返回的错误码（如 "UNAVAILABLE"），可能为 None
        error_message: 服务器返回的错误信息，可能为 None
        retryable: 服务器指示是否可重试，可能为 None
    """

    def __init__(
        self,
        message: str,
        error_code: str | None = None,
        error_message: str | None = None,
        retryable: bool | None = None,
    ):
        super().__init__(message)
        self.error_code = error_code
        self.error_message = error_message
        self.retryable = retryable


class BotWebSocketClient:
    """Bot WebSocket 客户端（纯异步版本）

    所有方法均为 async，不使用 threading，不会阻塞 event loop。
    """

    PROTOCOL_VERSION = 3

    def __init__(
        self,
        uri: str,
        client_id: str | None = None,
        client_version: str = "1.0.0",
        platform: str = "python",
        mode: str = "cli",
        headers: dict[str, str] | None = None,
    ):
        self.uri = uri
        self.client_id = client_id or f"client-{uuid.uuid4().hex[:8]}"
        self.client_version = client_version
        self.platform = platform
        self.mode = mode
        self.headers = headers or {}

        self._ws: ClientConnection | None = None
        self._recv_task: asyncio.Task[None] | None = None
        self._request_id = 0
        self._pending_requests: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._event_handlers: dict[str, EventHandler] = {}
        self._disconnect_callback: EventHandler | None = None
        self._connected = False
        self._handshake_complete = False
        self._server_info: dict[str, Any] | None = None
        self._features: dict[str, Any] | None = None

    @property
    def connected(self) -> bool:
        return self._connected and self._handshake_complete

    @property
    def server_info(self) -> dict[str, Any] | None:
        return self._server_info

    @property
    def features(self) -> dict[str, Any] | None:
        return self._features

    # ── 公开 API ──────────────────────────────────────────────────────────

    async def connect(
        self,
        timeout: float = 10.0,
        disable_ssl_verify: bool = True,
        open_timeout: float = 5.0,
    ) -> dict[str, Any]:
        """连接到服务器并完成握手

        Args:
            timeout: 整体握手超时时间（秒），包括 TCP 连接 + HTTP 升级 + 等待握手响应
            disable_ssl_verify: 是否禁用 SSL 证书验证（仅用于测试）
            open_timeout: WebSocket 连接建立（TCP + HTTP 升级）超时时间（秒），
                None 表示与 timeout 一致

        Returns:
            握手响应 payload
        """
        if self._ws is not None:
            raise RuntimeError("Already connected")

        headers = self._get_default_headers()
        logger.info(f"Connecting to: {self.uri}")

        if is_dev():
            iam_token = os.getenv("IAM_TOKEN")
            headers["Cookie"] = f"iam_token={iam_token}"
            logger.info("local dev, add iam token for connection")

        # SSL 配置
        ssl_context: ssl.SSLContext | None = None
        if self.uri.startswith("wss://"):
            ssl_context = ssl.create_default_context()
            if disable_ssl_verify:
                ssl_context.check_hostname = False
                ssl_context.verify_mode = ssl.CERT_NONE

        # 建立 WebSocket 连接
        # ping_interval=None: 禁用协议层 ping/pong 心跳，
        # 因为服务端（FastAPI/Starlette）不会自动回复 pong，
        # 会导致 keepalive ping timeout 断连。
        # 连接存活性由服务端应用层 tick 事件（每 30s）保证。
        additional_headers = websockets.Headers(headers)
        self._ws = await asyncio.wait_for(
            websockets.connect(
                self.uri,
                additional_headers=additional_headers,
                ssl=ssl_context,
                open_timeout=open_timeout,
                ping_interval=None,
            ),
            timeout=timeout,
        )
        self._connected = True
        logger.info("WebSocket connection established successfully")

        # 启动后台接收循环
        self._recv_task = asyncio.create_task(self._recv_loop())

        # 发送 connect 握手请求
        request_id = self._next_request_id()
        connect_params = {
            "minProtocol": self.PROTOCOL_VERSION,
            "maxProtocol": self.PROTOCOL_VERSION,
            "client": {
                "id": self.client_id,
                "version": self.client_version,
                "platform": self.platform,
                "mode": self.mode,
            },
        }

        request_frame = {
            "type": "req",
            "id": request_id,
            "method": "connect",
            "params": connect_params,
        }

        # 注册 Future 等待响应
        loop = asyncio.get_running_loop()
        future: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._pending_requests[request_id] = future

        await self._ws.send(json.dumps(request_frame))

        # 等待握手响应
        try:
            response = await asyncio.wait_for(future, timeout=timeout)
        except TimeoutError:
            self._pending_requests.pop(request_id, None)
            raise TimeoutError("Handshake timeout")
        finally:
            self._pending_requests.pop(request_id, None)

        if not response.get("ok"):
            error = response.get("error", {})
            raise RuntimeError(
                f"Handshake failed: {error.get('code')} - {error.get('message')}"
            )

        payload: dict[str, Any] = response.get("payload", {})
        self._server_info = payload.get("server", {})
        self._features = payload.get("features", {})
        self._handshake_complete = True

        return payload

    async def chat_send(
        self,
        session_key: str,
        message: str,
        timeout_ms: int | None = None,
        auth_token: str | None = None,
        app_id: str | None = None,
        chat_metadata: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """发送聊天消息"""
        params: dict[str, Any] = {
            "sessionKey": session_key,
            "message": message,
            "permissionMode": "bypassPermissions",
        }
        if timeout_ms:
            params["timeoutMs"] = str(timeout_ms)
        if app_id:
            params["appId"] = app_id
        if chat_metadata:
            params.update(chat_metadata)
        params["x-iam-token"] = auth_token or "OPEN_API:NOT_PROVIDED"

        result = await self._send_request(
            "chat.send",
            params,
            timeout=min(timeout_ms / 1000, 120) if timeout_ms else 120,
        )

        if not result.get("ok"):
            error = result.get("error", {})
            raise ChatRequestError(
                message=f"chat.send failed: {error.get('code')} - {error.get('message')}",
                error_code=error.get("code"),
                error_message=error.get("message"),
                retryable=error.get("retryable"),
            )

        return result

    async def chat_inject(
        self,
        session_key: str,
        message: str,
        timeout_ms: int | None = None,
        auth_token: str | None = None,
        chat_metadata: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """注入聊天消息"""
        params: dict[str, Any] = {
            "sessionKey": session_key,
            "message": message,
        }
        if timeout_ms:
            params["timeoutMs"] = str(timeout_ms)
        if chat_metadata:
            params.update(chat_metadata)
        params["x-iam-token"] = auth_token or "OPEN_API:NOT_PROVIDED"

        result = await self._send_request(
            "chat.inject",
            params,
            timeout=min(timeout_ms / 1000, 120) if timeout_ms else 120,
        )

        if not result.get("ok"):
            error = result.get("error", {})
            raise ChatRequestError(
                message=f"chat.inject failed: {error.get('code')} - {error.get('message')}",
                error_code=error.get("code"),
                error_message=error.get("message"),
                retryable=error.get("retryable"),
            )

        return result

    async def chat_abort(
        self,
        session_key: str,
        run_id: str | None = None,
    ) -> dict[str, Any]:
        """中止聊天"""
        params: dict[str, Any] = {"sessionKey": session_key}
        if run_id:
            params["runId"] = run_id

        return await self._send_request("chat.abort", params)

    async def session_reset(self, session_key: str) -> dict[str, Any]:
        """重置会话"""
        return await self._send_request("sessions.reset", {"sessionKey": session_key})

    def on_event(self, event_name: str, handler: EventHandler) -> None:
        """注册事件处理器（支持同步和异步回调）"""
        self._event_handlers[event_name] = handler

    def on_disconnect(self, handler: EventHandler) -> None:
        """注册断连回调（支持同步和异步回调）。

        当 WebSocket 连接断开（正常或异常）时调用，
        用于通知上层立即感知断连而非依赖轮询。
        """
        self._disconnect_callback = handler

    async def close(self) -> None:
        """关闭连接"""
        if self._recv_task and not self._recv_task.done():
            self._recv_task.cancel()
            try:
                await self._recv_task
            except asyncio.CancelledError:
                pass
            self._recv_task = None

        if self._ws:
            await self._ws.close()
            self._ws = None

        self._connected = False
        self._handshake_complete = False

    # ── 私有方法 ──────────────────────────────────────────────────────────

    def _next_request_id(self) -> str:
        self._request_id += 1
        return str(self._request_id)

    def _get_default_headers(self) -> dict[str, str]:
        """构建默认 headers"""
        headers = {
            "User-Agent": "OpenClaw-Python-Client/1.0",
        }
        headers.update(self.headers)
        return headers

    async def _recv_loop(self) -> None:
        """后台消息接收循环（作为 asyncio Task 运行）"""
        assert self._ws is not None
        close_exc: Exception | None = None
        try:
            async for raw_message in self._ws:
                if isinstance(raw_message, bytes):
                    logger.warning(f"Received binary: {repr(raw_message[:100])}...")
                    continue
                await self._handle_message(raw_message)
        except websockets.ConnectionClosedOK:
            logger.info("WebSocket connection closed normally, uri=%s", self.uri)
        except Exception as e:
            close_exc = e
            logger.error("WebSocket recv loop error: %s, uri=%s", e, self.uri)
        finally:
            self._connected = False
            self._handshake_complete = False
            # 通知所有等待中的请求，透传原始异常
            for req_id, future in self._pending_requests.items():
                if not future.done():
                    future.set_exception(
                        close_exc
                        or RuntimeError("WebSocket connection closed unexpectedly")
                    )
            self._pending_requests.clear()
            # 通知上层连接断开（事件驱动，替代轮询）
            if self._disconnect_callback is not None:
                try:
                    result = self._disconnect_callback("disconnect", {})
                    if asyncio.iscoroutine(result):
                        # 在 recv_loop 已结束的上下文中调度协程
                        asyncio.ensure_future(result)
                except Exception as e:
                    logger.error("Disconnect callback error: %s, uri=%s", e, self.uri)

    async def _handle_message(self, message: str) -> None:
        """处理收到的文本消息"""
        try:
            logger.debug(f"Received text message: {message[:500]}...")
            data = json.loads(message)
            frame_type = data.get("type")
            logger.debug(f"Parsed message: type={frame_type}, keys={list(data.keys())}")

            if frame_type == "res":
                request_id = data.get("id")
                future = self._pending_requests.get(request_id)
                if future and not future.done():
                    future.set_result(data)

            elif frame_type == "event":
                event_name = data.get("event")
                payload = data.get("payload", {})

                handler = self._event_handlers.get(event_name)
                if handler:
                    try:
                        coro = handler(payload)
                        if asyncio.iscoroutine(coro):
                            await coro
                    except Exception as e:
                        logger.error(f"Event handler error for '{event_name}': {e}")

                wildcard_handler = self._event_handlers.get("*")
                if wildcard_handler:
                    try:
                        coro = wildcard_handler(event_name, payload)
                        if asyncio.iscoroutine(coro):
                            await coro
                    except Exception as e:
                        logger.error(f"Wildcard event handler error: {e}")

            elif frame_type == "hello-ok":
                self._server_info = data.get("server", {})
                self._features = data.get("features", {})
                self._handshake_complete = True
                logger.info(f"Handshake complete: server={self._server_info}")

            else:
                logger.warning(
                    f"Unknown frame type: {frame_type}, full message: {data}"
                )

        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON: {e}")

    async def _send_request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        timeout: float = 30.0,
    ) -> dict[str, Any]:
        """发送 RPC 请求并等待响应"""
        if not self._connected or self._ws is None:
            raise RuntimeError("Not connected")

        request_id = self._next_request_id()
        request_frame = {
            "type": "req",
            "id": request_id,
            "method": method,
            "params": params,
        }

        # 注册 Future 等待响应
        loop = asyncio.get_running_loop()
        future: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._pending_requests[request_id] = future

        await self._ws.send(json.dumps(request_frame))

        # 等待响应
        try:
            result = await asyncio.wait_for(future, timeout=timeout)
        except TimeoutError:
            self._pending_requests.pop(request_id, None)
            raise TimeoutError(f"Request {method} timed out")
        finally:
            self._pending_requests.pop(request_id, None)

        return result
