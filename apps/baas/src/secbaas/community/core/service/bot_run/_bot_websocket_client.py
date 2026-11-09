"""
OpenClaw WebSocket 客户端（纯异步版本）

基于 websockets 异步库实现的客户端，支持：
- 握手：发送 connect 请求，接收 hello-ok 响应
- 消息循环：发送 RequestFrame，接收 ResponseFrame 和 EventFrame
- 聊天：发送 chat.send 请求，接收流式事件

全部基于 asyncio，不使用任何 threading，不会阻塞 event loop。
"""

import asyncio
import dataclasses
import ipaddress
import json
import ssl
import uuid
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

import websockets
from websockets.asyncio.client import ClientConnection

from secbaas.community.api.bot_interaction import InteractionResolution
from secbaas.community.http_header import get_http_header_plugin
from secbaas.community.logger import get_logger
from secbaas.community.tracer import get_tracer_plugin

from ._interaction_protocol import (
    EngineInteractionResolveExchange,
    build_interaction_resolve_request,
)

logger = get_logger("core-bot-run")

# 事件处理器类型：同步或异步均可，不关心返回值
EventHandler = Callable[..., Any]


def _is_loopback_websocket_uri(uri: str) -> bool:
    """Return whether a WebSocket URI targets a local engine adapter."""
    hostname = urlsplit(uri).hostname
    if not hostname:
        return False
    if hostname.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


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

    async def _abort_connect_attempt(self) -> None:
        """Synchronously detach, then clean every partially-created resource.

        Failure and cancellation paths reuse this helper. The original exception is
        re-raised by the caller; cleanup errors are logged by stable class only and
        cannot replace the original failure.
        """
        recv_task = self._recv_task
        ws = self._ws
        self._recv_task = None
        self._ws = None
        self._connected = False
        self._handshake_complete = False
        for future in self._pending_requests.values():
            if not future.done():
                future.cancel()
        self._pending_requests.clear()

        if recv_task is not None and not recv_task.done():
            recv_task.cancel()
        if recv_task is not None:
            try:
                await recv_task
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                logger.warning(
                    "Failed to cancel handshake receive task: error_class=%s",
                    type(exc).__name__,
                )
        if ws is not None:
            try:
                await ws.close()
            except Exception as exc:
                logger.warning(
                    "Failed to close handshake WebSocket: error_class=%s",
                    type(exc).__name__,
                )

    async def _connect(self, **kwargs: Any) -> dict[str, Any]:
        """Create and validate a connection, cleaning all state on failure."""
        headers = self._get_default_headers()
        logger.info("Connecting to WebSocket endpoint")
        get_http_header_plugin().inject_header(headers)

        ssl_context: ssl.SSLContext | None = None
        if self.uri.startswith("wss://"):
            ssl_context = ssl.create_default_context()
            if kwargs["disable_ssl_verify"]:
                ssl_context.check_hostname = False
                ssl_context.verify_mode = ssl.CERT_NONE

        additional_headers = websockets.Headers(headers)
        connect_kwargs: dict[str, Any] = {
            "additional_headers": additional_headers,
            "ssl": ssl_context,
            "open_timeout": kwargs["open_timeout"],
            "ping_interval": None,
            "max_size": kwargs["max_size"],
        }
        if _is_loopback_websocket_uri(self.uri):
            connect_kwargs["proxy"] = None
            logger.debug("WebSocket connection uses direct loopback transport")

        request_id: str | None = None
        try:
            self._ws = await asyncio.wait_for(
                websockets.connect(self.uri, **connect_kwargs),
                timeout=kwargs["timeout"],
            )
            self._connected = True
            logger.info("WebSocket connection established successfully")
            self._recv_task = asyncio.create_task(self._recv_loop())

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
            loop = asyncio.get_running_loop()
            future: asyncio.Future[dict[str, Any]] = loop.create_future()
            self._pending_requests[request_id] = future
            await self._ws.send(json.dumps(request_frame))
            response = await asyncio.wait_for(future, timeout=kwargs["timeout"])
            if not response.get("ok"):
                error = response.get("error", {})
                logger.warning(
                    "Handshake rejected: error_code=%s",
                    error.get("code"),
                    exc_info=False,
                )
                raise RuntimeError("Handshake failed")
            payload: dict[str, Any] = response.get("payload", {})
            self._server_info = payload.get("server", {})
            self._features = payload.get("features", {})
            self._handshake_complete = True
            return payload
        except BaseException:
            await self._abort_connect_attempt()
            raise

    async def connect(
        self,
        timeout: float = 10.0,
        disable_ssl_verify: bool = True,
        open_timeout: float = 10.0,
        max_size: int = 10 * 2**20,
    ) -> dict[str, Any]:
        """Connect, complete protocol handshake, and own cleanup until success.

        Args:
            timeout: Overall handshake timeout in seconds.
            disable_ssl_verify: Disable SSL verification (used by tests).
            open_timeout: TCP/HTTP upgrade timeout in seconds.
            max_size: Maximum WebSocket message size in bytes.

        Returns:
            The handshake response payload.
        """
        if self._ws is not None:
            raise RuntimeError("Already connected")
        return await self._connect(
            timeout=timeout,
            disable_ssl_verify=disable_ssl_verify,
            open_timeout=open_timeout if open_timeout is not None else timeout,
            max_size=max_size,
        )

    async def chat_send(
        self,
        session_key: str,
        message: str,
        timeout_ms: int | None = None,
        auth_token: str | None = None,
        app_id: str | None = None,
        chat_metadata: dict[str, str] | None = None,
        attachments: list[Any] | None = None,
    ) -> dict[str, Any]:
        """发送聊天消息。

        纯数据边界：只返回服务端原始响应 dict，不做 ok 判断、不抛异常、不打日志。
        调用方（``_send_chat_request``）统一负责判断 ``ok`` 字段、打印 result、
        构造 ``ChatRequestError`` 并抛出。
        """
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
        if attachments:
            params["attachments"] = [
                dataclasses.asdict(a) if dataclasses.is_dataclass(a) else a
                for a in attachments
            ]
        params["x-iam-token"] = auth_token or "OPEN_API:NOT_PROVIDED"
        params["traceId"] = get_tracer_plugin().get_trace_id()

        return await self._send_request(
            "chat.send",
            params,
            timeout=min(timeout_ms / 1000, 120) if timeout_ms else 120,
        )

    async def chat_inject(
        self,
        session_key: str,
        message: str,
        timeout_ms: int | None = None,
        auth_token: str | None = None,
        chat_metadata: dict[str, str] | None = None,
        attachments: list[Any] | None = None,
    ) -> dict[str, Any]:
        """注入聊天消息。

        纯数据边界，语义同 ``chat_send``。
        """
        params: dict[str, Any] = {
            "sessionKey": session_key,
            "message": message,
        }
        if timeout_ms:
            params["timeoutMs"] = str(timeout_ms)
        if chat_metadata:
            params.update(chat_metadata)
        if attachments:
            params["attachments"] = [
                dataclasses.asdict(a) if dataclasses.is_dataclass(a) else a
                for a in attachments
            ]
        params["x-iam-token"] = auth_token or "OPEN_API:NOT_PROVIDED"
        params["traceId"] = get_tracer_plugin().get_trace_id()

        return await self._send_request(
            "chat.inject",
            params,
            timeout=min(timeout_ms / 1000, 120) if timeout_ms else 120,
        )

    async def interaction_resolve(
        self,
        *,
        interaction_id: str,
        resolution: InteractionResolution,
    ) -> EngineInteractionResolveExchange:
        """Resolve an interaction and return the exact validated RPC exchange."""
        request = build_interaction_resolve_request(
            request_id=self._next_request_id(),
            interaction_id=interaction_id,
            resolution=resolution,
        )
        request["params"]["traceId"] = get_tracer_plugin().get_trace_id()
        response = await self._send_request_frame(request, timeout=30.0)
        return EngineInteractionResolveExchange.from_frames(
            request=request,
            response=response,
        )

    async def chat_abort(
        self,
        session_key: str,
        run_id: str | None = None,
    ) -> dict[str, Any]:
        """中止聊天"""
        params: dict[str, Any] = {"sessionKey": session_key}
        if run_id:
            params["runId"] = run_id
        params["traceId"] = get_tracer_plugin().get_trace_id()

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
                    logger.warning(
                        "Received binary WebSocket frame: length=%d",
                        len(raw_message),
                    )
                    continue
                await self._handle_message(raw_message)
        except websockets.ConnectionClosedOK:
            logger.info("WebSocket connection closed normally")
        except Exception as e:
            close_exc = e
            logger.error("WebSocket recv loop failed: error_class=%s", type(e).__name__)
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
            if self._disconnect_callback is not None and self._ws is not None:
                try:
                    result = self._disconnect_callback("disconnect", {})
                    if asyncio.iscoroutine(result):
                        # 在 recv_loop 已结束的上下文中调度协程
                        asyncio.ensure_future(result)
                except Exception as e:
                    logger.error(
                        "Disconnect callback failed: error_class=%s",
                        type(e).__name__,
                    )

    async def _handle_message(self, message: str) -> None:
        """处理收到的文本消息"""
        try:
            logger.debug("Received text message: length=%d", len(message))
            data = json.loads(message)
            frame_type = data.get("type")
            logger.debug("Parsed WebSocket JSON frame")

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
                        logger.error(
                            "Event handler failed: error_class=%s",
                            type(e).__name__,
                        )

                wildcard_handler = self._event_handlers.get("*")
                if wildcard_handler:
                    try:
                        coro = wildcard_handler(event_name, payload)
                        if asyncio.iscoroutine(coro):
                            await coro
                    except Exception as e:
                        logger.error(
                            "Wildcard event handler failed: error_class=%s",
                            type(e).__name__,
                        )

            elif frame_type == "hello-ok":
                self._server_info = data.get("server", {})
                self._features = data.get("features", {})
                self._handshake_complete = True
                logger.info("Handshake complete")

            else:
                logger.warning("Unknown WebSocket frame ignored")

        except json.JSONDecodeError as e:
            logger.error(
                "Invalid WebSocket JSON frame: error_class=%s",
                type(e).__name__,
            )

    async def _send_request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        timeout: float = 30.0,
    ) -> dict[str, Any]:
        """发送 RPC 请求并等待响应"""
        if not self._connected or self._ws is None:
            raise RuntimeError("Not connected")

        request_frame = {
            "type": "req",
            "id": self._next_request_id(),
            "method": method,
            "params": params,
        }
        return await self._send_request_frame(request_frame, timeout=timeout)

    async def _send_request_frame(
        self,
        request_frame: dict[str, Any],
        *,
        timeout: float,
    ) -> dict[str, Any]:
        """Send an already-built request frame and await its matching response."""
        if not self._connected or self._ws is None:
            raise RuntimeError("Not connected")

        request_id = request_frame["id"]
        method = request_frame["method"]
        loop = asyncio.get_running_loop()
        future: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._pending_requests[request_id] = future
        await self._ws.send(json.dumps(request_frame))

        try:
            return await asyncio.wait_for(future, timeout=timeout)
        except TimeoutError:
            raise TimeoutError(f"Request {method} timed out")
        finally:
            self._pending_requests.pop(request_id, None)
