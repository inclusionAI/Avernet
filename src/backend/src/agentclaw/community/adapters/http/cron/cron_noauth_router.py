"""Cron 路由 — 手动触发 autoInitiate 定时任务。
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Query

from agentclaw.community.api.cron_relay_service import CronRelayServiceProtocol
from agentclaw.community.adapters.http.cron.schemas import ApiResponse
from agentclaw.community.di import Injected
from agentclaw.community.log import get_logger

logger = get_logger()

router = APIRouter(prefix="/api/public/cron", tags=["cron-noauth"])


@router.post("/auto-initiate/run", response_model=ApiResponse)
async def run_auto_initiate(
    bot_id: str = Query(..., description="所属 Bot ID"),
    user_id: str = Query(..., description="用户ID"),
    nick_name: str = Query("", description="用户花名，缺省用 user_id"),
    force: bool = Query(True, description="是否强制执行"),
    service: CronRelayServiceProtocol = Injected(CronRelayServiceProtocol),
) -> ApiResponse:
    """通过 bot_id 手动触发 autoInitiate 定时任务。

    自动查找该 bot 下类型为 autoInitiate 的 cron job 并触发执行。
    """
    # nick_name 为死参，缺省用 user_id 填充
    effective_nick_name = nick_name or user_id

    logger.info(
        "[run_auto_initiate] bot_id=%s, user_id=%s, force=%s",
        bot_id, user_id, force,
    )
    try:
        result = await service.find_auto_initiate_and_run(
            bot_id=bot_id,
            user_id=user_id,
            nick_name=effective_nick_name,
            force=force,
        )
        return ApiResponse(
            success=result.get("success", True),
            data=result.get("data"),
        )
    except ValueError as e:
        logger.warning("[run_auto_initiate] Business error: %s", e)
        return ApiResponse(success=False, message=str(e), error_code=400)
    except Exception as e:
        logger.error("[run_auto_initiate] Unexpected error: %s", e, exc_info=True)
        return ApiResponse(success=False, message=str(e), error_code=500)


@router.post("/auto-initiate/run-single", response_model=ApiResponse)
async def run_single_auto_initiate(
    bot_id: str = Query(..., description="所属 Bot ID"),
    user_id: str = Query(..., description="用户ID"),
    dima_url: str = Query(..., description="DIMA 需求 URL"),
    append_message: str = Query("", description="补充说明"),
    nick_name: str = Query("", description="用户花名，缺省用 user_id"),
    model: Optional[str] = Query(None, description="模型覆盖"),
    service: CronRelayServiceProtocol = Injected(CronRelayServiceProtocol),
) -> ApiResponse:
    """为单个 DIMA 需求直接发起会话。

    接收一个 DIMA 需求 URL，直接创建会话并发送消息。
    workflow 从 bot 的 template_config 中自动读取，无需传入。

    URL 格式示例:
        https://project.example.com/space/W26001121848/requirement?openWorkItemId=xxx
    """
    effective_nick_name = nick_name or user_id

    logger.info(
        "[run_single_auto_initiate] bot_id=%s, user_id=%s, dima_url=%s",
        bot_id, user_id, dima_url,
    )
    try:
        result = await service.run_single_auto_initiate(
            bot_id=bot_id,
            user_id=user_id,
            nick_name=effective_nick_name,
            dima_url=dima_url,
            append_message=append_message,
            model=model,
        )
        return ApiResponse(
            success=result.get("success", True),
            data=result.get("data"),
        )
    except ValueError as e:
        logger.warning("[run_single_auto_initiate] Business error: %s", e)
        return ApiResponse(success=False, message=str(e), error_code=400)
    except Exception as e:
        logger.error("[run_single_auto_initiate] Unexpected error: %s", e, exc_info=True)
        return ApiResponse(success=False, message=str(e), error_code=500)
