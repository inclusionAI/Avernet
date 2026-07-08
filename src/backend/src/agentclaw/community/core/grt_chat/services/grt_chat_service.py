"""
GRT Chat service — provides sandbox resolution and engine WebSocket connection for bots.
"""
from __future__ import annotations

import asyncio
import json
import ssl
import uuid
from dataclasses import dataclass
from typing import AsyncIterator, Optional

import websockets
from injector import inject
from websockets import ClientConnection

from agentclaw.community.core.devices.repository import DeviceBindingRepository
from agentclaw.community.core.bot_management.repository.protocol import BotRepository
from agentclaw.community.plugin_api.sandbox_runtime import SandboxRuntimeClient

from agentclaw.community.log import get_logger
logger = get_logger()

# WS proxy-pass token lifetime (mirrors the device conn-info default).
_WS_TOKEN_TTL_SECONDS = 120 * 60


@dataclass
class ChatResult:
    """chat.send 的最终结果"""
    text: str
    run_id: str = ""
    session_key: str = ""
    timestamp: int = 0


@dataclass
class ChatStreamEvent:
    """chat_send_stream 产出的流式事件"""
    state: str  # "delta" | "final" | "error" | "aborted"
    text: str = ""
    run_id: str = ""
    session_key: str = ""
    timestamp: int = 0
    error_message: str = ""


class GrtChatService:
    """GRT Chat 核心服务"""

    @inject
    def __init__(
        self,
        bot_repo: BotRepository,
        device_binding_repo: DeviceBindingRepository,
        sandbox_client: SandboxRuntimeClient,
    ):
        self._bot_repo = bot_repo
        self._device_binding_repo = device_binding_repo
        self._sandbox_client = sandbox_client

    def _get_device_binding_repo(self) -> DeviceBindingRepository:
        return self._device_binding_repo

    def get_sandbox_id(self, bot_id: str, owner_id:str) -> Optional[str]:
        """根据 bot_id 获取 sandbox_id。

        流程:
          1. 通过 bot_repository.get_by_id(bot_id) 获取 bot 记录，取 binding_id
          2. 通过 device_binding_repository.get_by_id(binding_id) 获取 device_props
          3. 从 device_props 中提取 sandbox_id

        Args:
            bot_id: Bot ID
            owner_id: owner_id

        Returns:
            sandbox_id 字符串，找不到时返回 None
        """
        # 1. 查 bot 记录
        bot = self._bot_repo.get_by_id_and_owner(bot_id, owner_id)
        if bot is None:
            logger.warning(f"[get_sandbox_id] bot not found: bot_id={bot_id}")
            return None

        binding_id = bot.get("binding_id")
        if binding_id is None:
            logger.warning(f"[get_sandbox_id] bot has no binding_id: bot_id={bot_id}")
            return None

        # 2. 查 device binding 记录
        device_binding_repo = self._get_device_binding_repo()
        binding = device_binding_repo.get_by_id(int(binding_id))
        if binding is None:
            logger.warning(f"[get_sandbox_id] device binding not found: binding_id={binding_id}")
            return None

        # 3. 从 device_props 中提取 sandbox_id
        device_props = binding.device_props or {}
        sandbox_id = device_props.get("sandbox_id")
        if sandbox_id is None:
            logger.warning(f"[get_sandbox_id] sandbox_id not in device_props: binding_id={binding_id}")
            return None

        logger.info(f"[get_sandbox_id] resolved: bot_id={bot_id} -> binding_id={binding_id} -> sandbox_id={sandbox_id}")
        return sandbox_id

    async def connect_engine_ws(self, sandbox_id: str) -> ClientConnection:
        """通过 sandbox_id 建立到 Engine /api/openclaw/ws 的 WebSocket 连接。

        通过 Arca 代理服务转发，请求头中携带 x-proxypass-token 进行鉴权。

        Args:
            sandbox_id: Arca sandbox ID（如 "ARCA-SANDBOX-xxx@0"）

        Returns:
            已建立的 ClientConnection 连接

        Raises:
            ConnectionError: 连接失败时抛出
        """
        # 构建 WebSocket URL: wss://{proxy}/proxypass/{target}/api/openclaw/ws
        # 代理 base / target / 鉴权 token 由注入的 SandboxRuntimeClient 提供
        # （vendor 细节下沉到 prod 实现）。
        base_url = self._sandbox_client.proxy_base_url()
        conn = self._sandbox_client.build_proxy_connection(
            sandbox_id=sandbox_id, ttl_seconds=_WS_TOKEN_TTL_SECONDS
        )
        # https -> wss, http -> ws
        ws_base = base_url.replace("https://", "wss://").replace("http://", "ws://")
        ws_url = f"{ws_base}/proxypass/{conn.target}/api/openclaw/ws"
        headers = {"x-proxypass-token": conn.token}

        logger.info(f"[connect_engine_ws] connecting: sandbox_id={sandbox_id}, url={ws_url}")

        # SSL 配置
        ssl_context = None
        if ws_url.startswith("wss://"):
            ssl_context = ssl.create_default_context()
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE

        try:
            ws = await asyncio.wait_for(
                websockets.connect(
                    ws_url,
                    additional_headers=headers,
                    ssl=ssl_context,
                    max_size=2 * 1024 * 1024,
                ),
                timeout=30,
            )
            logger.info(f"[connect_engine_ws] WebSocket connected: sandbox_id={sandbox_id}")
        except asyncio.TimeoutError:
            raise ConnectionError(f"WebSocket connection timeout: {ws_url}")
        except Exception as e:
            raise ConnectionError(f"WebSocket connection failed: {e}")

        # 发送协议握手（Engine 要求第一条消息必须是 connect）
        try:
            connect_id = str(uuid.uuid4())
            connect_frame = {
                "type": "req",
                "id": connect_id,
                "method": "connect",
                "params": {
                    "minProtocol": 3,
                    "maxProtocol": 3,
                    "client": {
                        "id": "cli",
                        "version": "1.0.0",
                        "platform": "MacIntel",
                        "mode": "cli",
                    },
                    "role": "operator",
                    "x-moltis-mcp-token": conn.token,
                    "scopes": [
                        "operator.admin",
                        "operator.read",
                        "operator.write",
                    ],
                },
            }
            logger.info(f"[connect_engine_ws] sending connect frame: {json.dumps(connect_frame)}")
            await ws.send(json.dumps(connect_frame))

            raw = await asyncio.wait_for(ws.recv(), timeout=10)
            logger.info(f"[connect_engine_ws] handshake response: {raw}")
            resp = json.loads(raw)
            if not resp.get("ok", False):
                error_msg = resp.get("error", {}).get("message", "unknown error")
                await ws.close()
                raise ConnectionError(f"Handshake failed: {error_msg}")

            logger.info(f"[connect_engine_ws] handshake ok: sandbox_id={sandbox_id}")
            return ws
        except ConnectionError:
            raise
        except Exception as e:
            await ws.close()
            raise ConnectionError(f"Handshake failed: {e}")

    async def chat_send(
        self,
        ws: ClientConnection,
        message: str,
        session_key: str = "agent:main:default",
        timeout: float = 300,
    ) -> ChatResult:
        """通过已建立的 WebSocket 连接发送聊天请求，等待流式结果完成后返回。

        Args:
            ws: 已连接的 WebSocket（由 connect_engine_ws 返回）
            message: 提问内容
            session_key: 会话标识，默认 "agent:main:default"
            timeout: 等待最终结果的超时时间（秒），默认 300

        Returns:
            ChatResult 包含最终的文本回复

        Raises:
            TimeoutError: 等待结果超时
            ConnectionError: WebSocket 连接断开
            RuntimeError: 收到错误事件或异常状态
        """
        request_id = str(uuid.uuid4())

        # 构建请求帧
        request_frame = {
            "type": "req",
            "id": request_id,
            "method": "chat.send",
            "params": {
                "sessionKey": session_key,
                "message": message,
                "deliver": False,
                "idempotencyKey": request_id,
            },
        }

        logger.info(f"[chat_send] sending: id={request_id}, message={message[:50]}")
        await ws.send(json.dumps(request_frame))

        # 接收事件流，直到收到 state=final/error/aborted
        accumulated_text = ""
        run_id = ""
        timestamp = 0
        deadline = asyncio.get_event_loop().time() + timeout
        while True:
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                raise TimeoutError(f"[chat_send] timeout waiting for final result: id={request_id}")

            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
            except asyncio.TimeoutError:
                raise TimeoutError(f"[chat_send] timeout waiting for final result: id={request_id}")
            except websockets.ConnectionClosed as e:
                raise ConnectionError(f"[chat_send] WebSocket closed: {e}")

            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                logger.warning(f"[chat_send] invalid JSON frame, skipping")
                continue

            frame_type = data.get("type")

            # 处理 RPC 响应（chat.send 的确认）
            if frame_type == "res":
                if data.get("id") == request_id:
                    if not data.get("ok", True):
                        error = data.get("error", {})
                        raise RuntimeError(
                            f"[chat_send] request rejected: {error.get('message', 'unknown error')}"
                        )
                    logger.debug(f"[chat_send] request accepted: id={request_id}")
                continue

            # 处理事件帧
            if frame_type == "event" and data.get("event") == "chat":
                payload = data.get("payload", {})
                state = payload.get("state", "")
                run_id = payload.get("runId", run_id)

                if state == "delta":
                    # 流式增量：累积文本
                    msg = payload.get("message", {})
                    contents = msg.get("content", [])
                    for c in contents:
                        if c.get("type") == "text":
                            accumulated_text = c.get("text", "")

                elif state == "final":
                    # 最终结果
                    msg = payload.get("message", {})
                    timestamp = msg.get("timestamp", 0)
                    contents = msg.get("content", [])
                    for c in contents:
                        if c.get("type") == "text":
                            accumulated_text = c.get("text", "")

                    logger.info(
                        f"[chat_send] final: id={request_id}, run_id={run_id}, "
                        f"text_len={len(accumulated_text)}"
                    )
                    return ChatResult(
                        text=accumulated_text,
                        run_id=run_id,
                        session_key=payload.get("sessionKey", session_key),
                        timestamp=timestamp,
                    )

                elif state == "error":
                    error_msg = payload.get("errorMessage", "Unknown error")
                    raise RuntimeError(f"[chat_send] agent error: {error_msg}")

                elif state == "aborted":
                    raise RuntimeError("[chat_send] chat aborted")

    async def chat_send_stream(
        self,
        ws: ClientConnection,
        message: str,
        session_key: str = "agent:main:default",
        timeout: float = 300,
    ) -> AsyncIterator[ChatStreamEvent]:
        """流式版本的 chat_send，yield 每个 delta/final 事件。

        Args:
            ws: 已连接的 WebSocket
            message: 提问内容
            session_key: 会话标识
            timeout: 超时时间（秒）

        Yields:
            ChatStreamEvent — state 为 delta/final/error/aborted
        """
        request_id = str(uuid.uuid4())

        request_frame = {
            "type": "req",
            "id": request_id,
            "method": "chat.send",
            "params": {
                "sessionKey": session_key,
                "message": message,
                "deliver": False,
                "idempotencyKey": request_id,
            },
        }

        logger.info(f"[chat_send_stream] sending: id={request_id}, message={message[:50]}")
        await ws.send(json.dumps(request_frame))

        run_id = ""
        deadline = asyncio.get_event_loop().time() + timeout
        while True:
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                raise TimeoutError(f"[chat_send_stream] timeout: id={request_id}")

            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
            except asyncio.TimeoutError:
                raise TimeoutError(f"[chat_send_stream] timeout: id={request_id}")
            except websockets.ConnectionClosed as e:
                raise ConnectionError(f"[chat_send_stream] WebSocket closed: {e}")

            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                logger.warning("[chat_send_stream] invalid JSON frame, skipping")
                continue

            frame_type = data.get("type")

            # RPC 响应
            if frame_type == "res":
                if data.get("id") == request_id:
                    if not data.get("ok", True):
                        error = data.get("error", {})
                        raise RuntimeError(
                            f"[chat_send_stream] request rejected: {error.get('message', 'unknown error')}"
                        )
                    logger.debug(f"[chat_send_stream] request accepted: id={request_id}")
                continue

            # chat 事件帧
            if frame_type == "event" and data.get("event") == "chat":
                payload = data.get("payload", {})
                state = payload.get("state", "")
                run_id = payload.get("runId", run_id)

                if state == "delta":
                    msg = payload.get("message", {})
                    text = ""
                    for c in msg.get("content", []):
                        if c.get("type") == "text":
                            text = c.get("text", "")
                    yield ChatStreamEvent(
                        state="delta", text=text, run_id=run_id,
                        session_key=payload.get("sessionKey", session_key),
                    )

                elif state == "final":
                    msg = payload.get("message", {})
                    text = ""
                    for c in msg.get("content", []):
                        if c.get("type") == "text":
                            text = c.get("text", "")
                    yield ChatStreamEvent(
                        state="final", text=text, run_id=run_id,
                        session_key=payload.get("sessionKey", session_key),
                        timestamp=msg.get("timestamp", 0),
                    )
                    return

                elif state == "error":
                    error_msg = payload.get("errorMessage", "Unknown error")
                    yield ChatStreamEvent(
                        state="error", run_id=run_id, error_message=error_msg,
                    )
                    return

                elif state == "aborted":
                    yield ChatStreamEvent(state="aborted", run_id=run_id)
                    return

    @staticmethod
    async def disconnect(ws: ClientConnection) -> None:
        """关闭 WebSocket 连接。

        Args:
            ws: 由 connect_engine_ws 创建的 WebSocket 连接
        """
        try:
            await ws.close()
            logger.info("[disconnect] WebSocket connection closed")
        except Exception as e:
            logger.warning(f"[disconnect] error closing WebSocket: {e}")
