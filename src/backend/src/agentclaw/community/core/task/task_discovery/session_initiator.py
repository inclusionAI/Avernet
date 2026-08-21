"""SessionInitiator — 创建 engine session + WebSocket 注入发现提示消息。

两步流程：
  Step 1 — CronRelayService.forward_request(POST /api/sessions) 创建 session
  Step 2 — WebSocket 连 engine → connect 握手 → chat.send 发现提示消息

WebSocket 协议参考 ``test_create_session_e2e.py`` 的已验证实现：
  connect(proto3 握手) → chat.send(sessionKey, message) → 可选等 final

消息注入失败仅 log warning — session 已创建 = 主流程成功。
用户通过通知 deep_link 打开 session 后仍可手动交互。
"""
from __future__ import annotations

import asyncio
import json
import os
from typing import Any, Protocol

import httpx
import websockets

from agentclaw.community.core.task.task_discovery.models import (
    DiscoveredTask,
    DiscoverySession,
)
from agentclaw.community.log import get_logger

logger = get_logger()

#: 默认 backend 地址（用于查 bot connection 解析 engine target）
_DEFAULT_BACKEND_URL = "http://localhost:8888"

#: 默认前端 workbench 端口
_DEFAULT_FRONTEND_PORT = "8000"

#: WebSocket 协议常量
_WS_PROTOCOL = 3
_WS_HANDSHAKE_TIMEOUT = 10.0
_WS_SEND_TIMEOUT = 10.0
_WS_REPLY_TIMEOUT = 60.0  # 仅 wait_for_reply=True 时使用


class SessionInitiator(Protocol):
    """Engine session 创建+消息注入接口。"""

    async def initiate_session(
        self,
        tasks: list[DiscoveredTask],
        *,
        bot_id: str,
        owner_id: str,
        agent_id: str,
        model: str | None = None,
    ) -> DiscoverySession:
        """为发现任务创建 engine session 并注入发现提示消息。"""
        ...


class CronRelaySessionInitiator:
    """通过 relay 通道创建 session + WebSocket 注入发现消息。

    流程：
      Step 1 — CronRelayService.forward_request(POST /api/sessions) 创建 session
      Step 2 — 解析 engine target 地址
      Step 3 — WebSocket 连 engine → connect 握手 → chat.send 发现提示消息
      Step 4 — 返回 session_id + session_url

    agent 回复策略：
      - 默认发完即走（fire message）— 不等 state=final
      - 用户打开 session 时 bot 回复可能已生成或正在生成
      - 可选 wait_for_reply=True 等待 final（阻塞，用于测试）
    """

    def __init__(
        self,
        cron_relay: Any,
        frontend_url: str | None = None,
        wait_for_reply: bool = False,
    ):
        self._cron_relay = cron_relay
        self._frontend_url = frontend_url or os.environ.get(
            "FRONTEND_URL",
            f"http://localhost:{_DEFAULT_FRONTEND_PORT}",
        )
        self._backend_url = os.environ.get(
            "BACKEND_URL", _DEFAULT_BACKEND_URL,
        )
        self._wait_for_reply = wait_for_reply

    async def initiate_session(
        self,
        tasks: list[DiscoveredTask],
        *,
        bot_id: str,
        owner_id: str,
        agent_id: str,
        model: str | None = None,
    ) -> DiscoverySession:
        """创建 session + 注入发现消息。

        Steps:
            1. 构造 session body（title + extInfo）→ relay 创建 session
            2. 从 relay 响应中提取 session_id + engine target
            3. WebSocket 连 engine → chat.send 发现提示消息
            4. 返回 DiscoverySession
        """
        first_task = tasks[0]
        task_count = len(tasks)
        title = (
            f"为你发现了 {task_count} 件可能有意义的事情"
            if task_count > 1
            else first_task.project_name
        )

        # ── Step 1: 创建 session ──────────────────────────────
        body: dict[str, Any] = {
            "title": title,
            "user_id": owner_id,
            "agent_id": agent_id,
            "extInfo": {
                "source": "task_discovery",
                "task_count": task_count,
                "discovery_date": first_task.dt,
                "tasks": [t.to_session_ext_info() for t in tasks],
            },
        }
        if model:
            body["model"] = model

        result = await self._cron_relay.forward_request(
            bot_id=bot_id,
            user_id=owner_id,
            nick_name=owner_id,
            method="POST",
            path="/api/sessions",
            body=body,
        )

        if not result.get("success"):
            raise RuntimeError(
                f"engine session creation failed: {result.get('message', result)}"
            )

        session_data = result.get("data", {})
        session_id = (
            session_data.get("id")
            or session_data.get("session_id", "")
        )
        if not session_id:
            raise RuntimeError(f"engine response missing session id: {result}")

        logger.info(
            "[task_discovery] session created: id=%s (bot=%s)",
            session_id, bot_id,
        )

        # ── Step 2: 解析 engine target ───────────────────────
        engine_target = await self._extract_engine_target(bot_id, owner_id)
        if not engine_target:
            logger.warning(
                "[task_discovery] no engine target for bot=%s, "
                "session created but message injection skipped",
                bot_id,
            )
        else:
            # ── Step 3: WebSocket 注入发现消息 ────────────────
            discovery_prompt = self._build_discovery_prompt(tasks)
            await self._ws_send_message(
                engine_target, session_id, discovery_prompt,
            )

        session_url = self._build_session_url(session_id, agent_id)

        return DiscoverySession(
            task_id=first_task.task_id,
            session_id=session_id,
            session_url=session_url,
        )

    # ── Engine target 解析 ────────────────────────────────────

    async def _extract_engine_target(
        self, bot_id: str, owner_id: str,
    ) -> str | None:
        """通过 backend API 查 per-bot engine 的 target 地址。

        复用 HttpSessionCreator._resolve_engine_target 的逻辑：
        1. GET /api/bots/{bot_id} → 拿 binding_id
        2. GET /api/v1/devices/{binding_id}/connection → 拿 target

        Returns:
            如 ``localhost:20010``，失败返回 None。
        """
        try:
            async with httpx.AsyncClient(timeout=10.0) as cli:
                bot_resp = await cli.get(
                    f"{self._backend_url}/api/bots/{bot_id}",
                    params={"owner_id": owner_id},
                    headers={"x-user-id": owner_id},
                )
                bot_resp.raise_for_status()
                binding_id = (
                    bot_resp.json().get("data") or {}
                ).get("binding_id")
                if not binding_id:
                    return None
                conn_resp = await cli.get(
                    f"{self._backend_url}/api/v1/devices/{binding_id}/connection",
                    headers={"x-user-id": owner_id},
                )
                conn_resp.raise_for_status()
                return (
                    conn_resp.json().get("data") or {}
                ).get("target") or None
        except Exception:
            return None

    # ── WebSocket 消息注入 ────────────────────────────────────

    async def _ws_send_message(
        self, target: str, session_key: str, message: str,
    ) -> None:
        """WebSocket 连接 engine → 握手 → chat.send → 关闭。

        协议同 test_create_session_e2e.py:128-179：connect(proto3) → chat.send。
        默认发完即走（不等 final）；wait_for_reply=True 时等待 agent 回复。

        仅 log warning，不抛异常 — 消息注入失败不影响 session 创建结果。
        """
        uri = f"ws://{target}/api/openclaw/ws"
        connect_params = {
            "minProtocol": _WS_PROTOCOL,
            "maxProtocol": _WS_PROTOCOL,
            "client": {
                "id": "task-discovery-initiator",
                "version": "1.0.0",
                "platform": "linux",
                "mode": "operator",
            },
            "role": "operator",
        }

        try:
            async with websockets.connect(
                uri, open_timeout=_WS_HANDSHAKE_TIMEOUT,
            ) as ws:
                # 1) 握手
                await ws.send(json.dumps({
                    "type": "req", "id": "1",
                    "method": "connect",
                    "params": connect_params,
                }))
                hello = json.loads(await asyncio.wait_for(
                    ws.recv(), timeout=_WS_HANDSHAKE_TIMEOUT,
                ))
                if not hello.get("ok"):
                    logger.warning(
                        "[task_discovery] WS handshake failed: %s",
                        json.dumps(hello)[:200],
                    )
                    return

                # 2) chat.send 发送发现提示消息
                await ws.send(json.dumps({
                    "type": "req", "id": "2",
                    "method": "chat.send",
                    "params": {
                        "sessionKey": session_key,
                        "message": message,
                    },
                }))
                ack = json.loads(await asyncio.wait_for(
                    ws.recv(), timeout=_WS_SEND_TIMEOUT,
                ))
                if not ack.get("ok"):
                    logger.warning(
                        "[task_discovery] WS chat.send rejected: %s",
                        json.dumps(ack)[:200],
                    )
                    return

                logger.info(
                    "[task_discovery] WS message injected: session=%s",
                    session_key,
                )

                # 3) 可选：等待 agent 回复
                if self._wait_for_reply:
                    await self._wait_for_final(ws, session_key)

        except Exception as exc:
            logger.warning(
                "[task_discovery] WS message injection failed for "
                "session %s: %s (session already created, user can "
                "interact manually)",
                session_key, exc,
            )

    async def _wait_for_final(self, ws: Any, session_key: str) -> None:
        """等待 chat agent 输出 state=final 事件。"""
        try:
            while True:
                raw = await asyncio.wait_for(
                    ws.recv(), timeout=_WS_REPLY_TIMEOUT,
                )
                data = json.loads(raw)
                if data.get("type") != "event":
                    continue
                if data.get("event") == "chat":
                    state = (data.get("payload") or {}).get("state")
                    if state in ("final", "error"):
                        break
        except asyncio.TimeoutError:
            logger.warning(
                "[task_discovery] WS reply timeout for session %s",
                session_key,
            )

    # ── 辅助方法 ──────────────────────────────────────────────

    def _build_discovery_prompt(self, tasks: list[DiscoveredTask]) -> str:
        """构造发现提示消息 — 作为 chat.send 的 message 发送给 bot。"""
        lines = ["/task 我为您发现了以下可能有意义的事情，请确认是否执行：\n"]
        for i, task in enumerate(tasks, 1):
            lines.append(f"{i}. 【{task.project_name}】")
            lines.append(f"   简介：{task.description}")
            lines.append(f"   业务场景：{task.business_scenario}")
            if task.work_item_url:
                lines.append(f"   关联需求：{task.work_item_url}")
            lines.append("")
        lines.append("请向用户展示以上任务，并询问是否确认执行。")
        return "\n".join(lines)

    def _build_session_url(self, session_id: str, agent_id: str) -> str:
        """构建前端 workbench session URL。"""
        base = self._frontend_url.rstrip("/")
        return (
            f"{base}/bcn/chat/session"
            f"?bot_uuid={agent_id}&id={agent_id}&session={session_id}"
        )


__all__ = ["SessionInitiator", "CronRelaySessionInitiator"]