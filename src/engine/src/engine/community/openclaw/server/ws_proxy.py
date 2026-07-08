"""
WebSocket Proxy - 四层透传代理

将 /api/openclaw/client 的流量透传到本地的 ws://127.0.0.1:18789/client，
实现将本地服务暴露到 0.0.0.0 上。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

import websockets
from websockets.asyncio.client import ClientConnection
from fastapi import WebSocket, WebSocketDisconnect, status
from starlette.websockets import WebSocketState

from engine.community.config import load_max_connections
from engine.community.shared import get_connection_limiter

log = logging.getLogger("ws-proxy")


class WSProxy:
    """WebSocket 四层透传代理"""

    def __init__(self, upstream_base: str):
        """
        初始化 WebSocket 代理

        Args:
            upstream_base: 上游 WebSocket 服务地址，默认 ws://127.0.0.1:18789
        """
        self._upstream_base = upstream_base.rstrip("/")

    async def handle_connection(
            self,
            client_ws: WebSocket,
            path: str = "/client",
            headers: Optional[dict] = None,
    ) -> None:
        """
        处理客户端连接，建立到上游的双向透传

        Args:
            client_ws: 客户端 WebSocket 连接
            path: 上游路径，默认 /client
            headers: 转发到上游的额外 headers
        """
        await client_ws.accept()

        # 限流检查
        max_conn = load_max_connections()
        limiter = get_connection_limiter()
        if not await limiter.try_acquire(max_conn):
            log.warning(f"[WSProxy] Connection rejected: limit reached (max={max_conn})")
            await client_ws.close(code=status.WS_1013_TRY_AGAIN_LATER, reason="连接数已达上限")
            return

        # 构造上游完整 URL
        upstream_url = f"{self._upstream_base}{path}"
        log.info(f"[WSProxy] Connecting to upstream: {upstream_url}")

        # 准备上游连接的 headers
        upstream_headers = {}
        if headers:
            upstream_headers.update(headers)

        upstream_ws = None

        try:
            # 建立到上游的 WebSocket 连接
            upstream_ws = await websockets.connect(
                upstream_url,
                additional_headers=upstream_headers,
                max_size=8 * 1024 * 1024,  # 8MB
            )

            log.info(f"[WSProxy] Connected to upstream: {upstream_url}")

            # 创建双向转发任务
            client_to_upstream = asyncio.create_task(
                self._forward_client_to_upstream(client_ws, upstream_ws)
            )
            upstream_to_client = asyncio.create_task(
                self._forward_upstream_to_client(upstream_ws, client_ws)
            )

            # 等待任意一个任务完成
            done, pending = await asyncio.wait(
                [client_to_upstream, upstream_to_client],
                return_when=asyncio.FIRST_COMPLETED,
            )

            # 取消未完成的任务
            for task in pending:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        except Exception as e:
            log.error(f"[WSProxy] Failed to connect upstream: {upstream_url}, error: {e}")
            try:
                if client_ws.client_state == WebSocketState.CONNECTED:
                    await client_ws.close(
                        code=status.WS_1013_TRY_AGAIN_LATER,
                        reason=f"Upstream unavailable: {e}",
                    )
            except Exception:
                pass

        finally:
            # 清理资源
            if upstream_ws:
                try:
                    await upstream_ws.close()
                except Exception as e:
                    log.debug(f"[WSProxy] Error closing upstream ws: {e}")

            await limiter.release()
            log.info("[WSProxy] Connection closed")

    async def _forward_client_to_upstream(
            self,
            client_ws: WebSocket,
            upstream_ws: ClientConnection,
    ) -> None:
        """将消息从客户端转发到上游"""
        try:
            while client_ws.client_state == WebSocketState.CONNECTED:
                # 接收客户端消息
                message = await client_ws.receive()

                if message["type"] == "websocket.receive":
                    if "text" in message:
                        await upstream_ws.send(message["text"])
                        log.debug(f"[WSProxy] C->U text: {len(message['text'])} bytes")
                    elif "bytes" in message:
                        await upstream_ws.send(message["bytes"])
                        log.debug(f"[WSProxy] C->U bytes: {len(message['bytes'])} bytes")

                elif message["type"] == "websocket.disconnect":
                    log.debug("[WSProxy] Client disconnect message received")
                    break

                elif message["type"] == "websocket.close":
                    log.debug("[WSProxy] Client sent close frame")
                    break

        except WebSocketDisconnect:
            log.debug("[WSProxy] Client disconnected")
        except Exception as e:
            log.debug(f"[WSProxy] Client->Upstream error: {e}")

    async def _forward_upstream_to_client(
            self,
            upstream_ws: ClientConnection,
            client_ws: WebSocket,
    ) -> None:
        """将消息从上游转发到客户端"""
        try:
            async for message in upstream_ws:
                if isinstance(message, str):
                    await client_ws.send_text(message)
                    log.debug(f"[WSProxy] U->C text: {len(message)} bytes")
                elif isinstance(message, bytes):
                    await client_ws.send_bytes(message)
                    log.debug(f"[WSProxy] U->C binary: {len(message)} bytes")

        except websockets.ConnectionClosed as e:
            log.debug(f"[WSProxy] Upstream connection closed: {e}")
        except Exception as e:
            log.debug(f"[WSProxy] Upstream->Client error: {e}")


# ── 全局代理实例 ────────────────────────────────────────────────────────

_proxy: Optional[WSProxy] = None


def get_proxy(upstream_base: str) -> WSProxy:
    """获取全局代理实例"""
    global _proxy
    if _proxy is None:
        _proxy = WSProxy(upstream_base=upstream_base)
    return _proxy


def reset_proxy() -> None:
    """重置全局代理实例"""
    global _proxy
    _proxy = None
