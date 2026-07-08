"""
Notify router — aggregates pending HITL interactions from all bots via Engine.

GET /api/v1/notify:
  1. Get user's active device bindings (bots with sandboxes)
  2. Parallel probe each bot's Engine /api/notify endpoint via proxypass
  3. Return grouped result: [{bot_id, bot_name, sandbox_id, notifications}]
"""
from __future__ import annotations

import asyncio
from typing import Any

import httpx
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from agentclaw.community.adapters.http.auth.dependencies import get_current_user
from agentclaw.community.adapters.http.auth.models import AuthenticatedUser
from agentclaw.community.core.notify.protocol import NotifyBotLister
from agentclaw.community.di import Injected
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.sandbox_runtime import SandboxRuntimeClient

logger = get_logger()

router = APIRouter(prefix="/api/v1/notify", tags=["notify"])


class NotifyEntry(BaseModel):
    """Single pending interaction item."""
    interactionId: str
    sessionKey: str
    runId: str
    kind: str
    prompt: str | None = None
    questions: list[dict[str, Any]] | None = None
    options: list[dict[str, Any]] | None = None
    subject: dict[str, Any] | None = None
    command: str | None = None
    cwd: str | None = None
    status: str
    createdAtMs: int
    expiresAtMs: int | None = None


class BotNotifySummary(BaseModel):
    """Notifications grouped by bot."""
    bot_id: str
    bot_name: str
    sandbox_id: str
    notifications: list[NotifyEntry]


class NotifySummaryResponse(BaseModel):
    success: bool
    data: list[BotNotifySummary]


async def _probe_bot_notify(
    bot_id: str,
    bot_name: str,
    sandbox_id: str,
    sandbox_client: SandboxRuntimeClient,
) -> BotNotifySummary | None:
    """Call Engine /api/notify via proxypass for a single bot."""
    # 没有 sandbox 的 bot —— 与 by-owner 一致仍然回一条记录,但不去探活,
    # 通知列表给空。这样前端能感知到 bot 存在但未绑定设备。
    if not sandbox_id:
        logger.info(f"[notify] bot={bot_id} has no sandbox, skipping probe")
        return BotNotifySummary(
            bot_id=bot_id,
            bot_name=bot_name,
            sandbox_id="",
            notifications=[],
        )

    try:
        # Local 模式：直接调用本地 Engine
        if sandbox_id == "local":
            url = "http://127.0.0.1:20003/api/notify"
            headers = {}
        else:
            # Prod/Pre 模式：通过设备运行时代理调用（vendor 细节在 prod 实现）
            req = sandbox_client.build_proxy_request(
                sandbox_id=sandbox_id, api_path="/api/notify"
            )
            url = req.url
            headers = req.headers

        logger.info(f"[notify] probing bot={bot_id} at {url}")

        # 使用 httpx 直接调用 Engine API
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(url, headers=headers)
            logger.info(f"[notify] bot={bot_id} response status={resp.status_code}")
            if resp.status_code != 200:
                logger.warning(f"[notify] HTTP {resp.status_code} for bot={bot_id}")
                return BotNotifySummary(
                    bot_id=bot_id,
                    bot_name=bot_name,
                    sandbox_id=sandbox_id,
                    notifications=[],
                )
            result = resp.json()

        if not result.get("success"):
            logger.warning(f"[notify] probe failed for bot={bot_id}: {result}")
            return BotNotifySummary(
                bot_id=bot_id,
                bot_name=bot_name,
                sandbox_id=sandbox_id,
                notifications=[],
            )

        notifications_data = result.get("data", [])
        logger.info(f"[notify] bot={bot_id} got {len(notifications_data)} notifications from engine")

        notifications = [
            NotifyEntry(**n) for n in notifications_data
        ] if notifications_data else []

        return BotNotifySummary(
            bot_id=bot_id,
            bot_name=bot_name,
            sandbox_id=sandbox_id,
            notifications=notifications,
        )
    except Exception as e:
        logger.error(f"[notify] error probing bot={bot_id}: {e}")
        return BotNotifySummary(
            bot_id=bot_id,
            bot_name=bot_name,
            sandbox_id=sandbox_id,
            notifications=[],
        )


@router.get("", response_model=NotifySummaryResponse)
async def get_notify_summary(
    user: AuthenticatedUser = Depends(get_current_user),
    bot_lister: NotifyBotLister = Injected(NotifyBotLister),
    sandbox_client: SandboxRuntimeClient = Injected(SandboxRuntimeClient),
):
    """
    Get aggregated pending notifications from all user bots.

    Returns a list of bot summaries, each containing the pending HITL
    interactions from that bot's engine.
    """
    user_id = user.staffId
    bot_mappings = bot_lister.list_bot_mappings(user_id)

    if not bot_mappings:
        return NotifySummaryResponse(success=True, data=[])

    # Parallel probe all bots
    summaries = await asyncio.gather(*[
        _probe_bot_notify(bot_id, bot_name, sandbox_id, sandbox_client)
        for bot_id, bot_name, sandbox_id in bot_mappings
    ])

    # Filter out None results and empty notification lists for cleaner response
    data = [s for s in summaries if s is not None]

    return NotifySummaryResponse(success=True, data=data)
