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
import json
import logging
import os
from contextlib import aclosing
from typing import Any, AsyncIterator, Awaitable, Callable, Dict, Optional

from engine.community.openclaw.protocol import (
    ErrorCodes,
    EventFrame,
    RequestFrame,
    ResponseFrame,
)
from engine.community.openclaw.config import OpenClawConfig

from engine.community.openclaw.client.gateway_connection import GatewayConnection
from engine.community.openclaw.client.chat_stream import _summarize_attachments

log = logging.getLogger("openclaw-client")
_DEBUG = os.getenv("OPENCLAW_DEBUG_EVENTS", "").lower() in {"1", "true", "yes", "on"}
_MAX_PRE_ACK_BYTES = 1_572_864


class OpenClawGatewayClient(GatewayConnection):
    """OpenClaw RPC and chat events over a managed gateway connection."""

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
        except (Exception, asyncio.CancelledError):
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
        on_ack: Callable[[ResponseFrame], Awaitable[None]] | None = None,
        *,
        wait_for_exec_approval: bool = False,
    ) -> AsyncIterator[Dict[str, Any]]:
        from engine.community.openclaw.client.chat_serialization import serialized_chat

        async with aclosing(serialized_chat(
            self, session_key, message, timeout_ms, idempotency_key,
            attachments, on_ack, max_pre_ack_bytes=_MAX_PRE_ACK_BYTES,
            wait_for_exec_approval=wait_for_exec_approval,
        )) as stream:
            async for event in stream:
                yield event

    async def chat_abort(
        self,
        session_key: str,
        run_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """中止聊天会话"""
        from engine.community.openclaw.client.chat_serialization import cancel_pending

        cancelled = await cancel_pending(self, session_key, run_id)
        params: Dict[str, Any] = {"sessionKey": session_key}
        if run_id:
            params["runId"] = run_id

        response = await self.send_request("chat.abort", params)
        if response.ok:
            payload = response.payload
            if cancelled:
                payload = {**(payload or {}), "aborted": True,
                           "runIds": list(dict.fromkeys([*(payload or {}).get("runIds", []), *cancelled]))}
            return {"success": True, "payload": payload}
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
                if client.connected:
                    return client
                log.warning("[pool] slot %d is unavailable after connect", idx)
            except Exception as e:
                log.warning("[pool] connect failed for slot %d: %s", idx, e)
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
