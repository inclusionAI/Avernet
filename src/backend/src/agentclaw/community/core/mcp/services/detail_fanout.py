"""一批 MCP 详情的并发投递:限流、异常兜底、成功/失败归类。

``MCPSyncService`` 有两条链路要把一批 MCP 详情推到**同一台**设备:
``_sync_mcp_details``(自己去 ``BotMCPProvider`` collect 列表)与
``sync_mcp_details_for_bot``(caller 给定 desired state)。两者只有入口和
并发上限不同,投递与结果归类完全一致,放这里共用。
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from agentclaw.community.log import get_logger

logger = get_logger()


def server_code_of(mcp: dict[str, Any]) -> str:
    """取 server_code —— MCP Center 给 camelCase,内部流转是 snake_case。"""
    return mcp.get("server_code") or mcp.get("serverCode") or ""


async def fan_out_mcp_details(
    *,
    mcps: list[dict[str, Any]],
    push_one: Callable[[dict[str, Any]], Awaitable[bool]],
    concurrency: int,
    bot_id: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """并发把 ``mcps`` 交给 ``push_one`` 投递,返回 ``(successes, failures)``。

    并发上限有两条理由,缺一不可:每条投递最终是一次设备侧阻塞 HTTP,放开会把
    单台引擎压垮;而投递经 ``asyncio.to_thread`` 落到 event loop 的默认线程池
    (``min(32, cpu_count + 4)``,全进程共享),放开也只是排队,并挤占其它
    ``to_thread`` 调用方。

    ``return_exceptions=True``:单条失败不能把兄弟任务留成 orphan——否则它们会在
    本函数返回后继续往设备投递。``CancelledError`` 来自上层,必须向上抛。
    """
    semaphore = asyncio.Semaphore(concurrency)

    async def deliver(mcp: dict[str, Any]) -> bool:
        async with semaphore:
            server_code = server_code_of(mcp)
            if not server_code:
                logger.warning(
                    "[MCPSyncService] 跳过无 server_code 的 MCP, bot_id=%s, mcp=%s",
                    bot_id, mcp,
                )
                return False
            try:
                return await push_one(mcp)
            except Exception as e:
                logger.error(
                    "[MCPSyncService] 同步 %s 异常, bot_id=%s, error=%s",
                    server_code, bot_id, e,
                )
                return False

    results = await asyncio.gather(
        *(deliver(mcp) for mcp in mcps), return_exceptions=True
    )

    successes: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    for mcp, result in zip(mcps, results, strict=True):
        if isinstance(result, asyncio.CancelledError):
            raise result
        if isinstance(result, Exception):
            logger.error(
                "[MCPSyncService] 任务异常 %s, bot_id=%s, error=%s",
                server_code_of(mcp), bot_id, result,
            )
            failures.append(mcp)
        elif result:
            successes.append(mcp)
        else:
            failures.append(mcp)
    return successes, failures
