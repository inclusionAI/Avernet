"""Bot common-config management router.

Thin adapter: request/response translation only. Writes pass decoded
``JsonValue`` straight to the service, which owns the on-disk JSON format of
``config_value``; read-side view shaping lives in ``converter.py``.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from agentclaw.community.adapters.http.auth.dependencies import require_operator
from agentclaw.community.adapters.http.auth.models import AuthenticatedUser
from agentclaw.community.adapters.http.bot_common_config.converter import (
    record_to_dict,
)
from agentclaw.community.adapters.http.bot_common_config.schemas import (
    BatchUpsertBotCommonConfigRequest,
    CreateBotCommonConfigRequest,
    GetBotCommonConfigRequest,
    DeleteBotCommonConfigRequest,
    UpdateBotCommonConfigRequest,
)
from agentclaw.community.api.bot_common_config_service import (
    BotCommonConfigEntry,
    BotCommonConfigServiceProtocol,
)
from agentclaw.community.di import Injected
from agentclaw.community.utils import env_utils

router = APIRouter(prefix="/api/v1/bot-common-config", tags=["bot-common-config"])


@router.get("/list")
async def list_bot_common_configs(
    bot_id: str | None = None,
    entity_id: str | None = None,
    config_key: str | None = None,
    page_num: int = 1,
    page_size: int = 100,
    _: AuthenticatedUser = Depends(require_operator),
    service: BotCommonConfigServiceProtocol = Injected(BotCommonConfigServiceProtocol),
):
    total, records = service.list_records(
        bot_id=bot_id,
        entity_id=entity_id,
        env=env_utils.get_current_env(),
        config_key=config_key,
        page_num=page_num,
        page_size=page_size,
    )
    return {
        "success": True,
        "message": "OK",
        "error_code": 200,
        "data": {
            "total": total,
            "items": [record_to_dict(r) for r in records],
        },
    }


@router.post("/get")
async def get_bot_common_config(
    request: GetBotCommonConfigRequest,
    _: AuthenticatedUser = Depends(require_operator),
    service: BotCommonConfigServiceProtocol = Injected(BotCommonConfigServiceProtocol),
):
    value = service.get_config(
        bot_id=request.bot_id,
        entity_id=request.entity_id,
        env=env_utils.get_current_env(),
        config_key=request.config_key,
    )
    if value is None:
        raise HTTPException(status_code=404, detail="配置不存在")
    return {
        "success": True,
        "message": "OK",
        "error_code": 200,
        "data": {**request.model_dump(), "config_value": value},
    }


@router.post("/create")
async def create_bot_common_config(
    request: CreateBotCommonConfigRequest,
    _: AuthenticatedUser = Depends(require_operator),
    service: BotCommonConfigServiceProtocol = Injected(BotCommonConfigServiceProtocol),
):
    try:
        config_id = service.create_record(
            bot_id=request.bot_id,
            entity_id=request.entity_id,
            env=env_utils.get_current_env(),
            config_key=request.config_key,
            value=request.config_value,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail="配置创建失败") from exc
    return {
        "success": True,
        "message": "配置已创建",
        "error_code": 200,
        "data": {"config_id": config_id},
    }


@router.post("/update")
async def update_bot_common_config(
    request: UpdateBotCommonConfigRequest,
    _: AuthenticatedUser = Depends(require_operator),
    service: BotCommonConfigServiceProtocol = Injected(BotCommonConfigServiceProtocol),
):
    if not service.update_record(config_id=request.id, value=request.config_value):
        raise HTTPException(status_code=404, detail="配置不存在")
    return {"success": True, "message": "配置已更新", "error_code": 200, "data": None}


@router.post("/upsert")
async def upsert_bot_common_config(
    request: CreateBotCommonConfigRequest,
    _: AuthenticatedUser = Depends(require_operator),
    service: BotCommonConfigServiceProtocol = Injected(BotCommonConfigServiceProtocol),
):
    config_id = service.upsert_record(
        bot_id=request.bot_id,
        entity_id=request.entity_id,
        env=env_utils.get_current_env(),
        config_key=request.config_key,
        value=request.config_value,
    )
    return {
        "success": True,
        "message": "配置已保存",
        "error_code": 200,
        "data": {"config_id": config_id},
    }


@router.post("/batch-upsert")
async def batch_upsert_bot_common_configs(
    request: BatchUpsertBotCommonConfigRequest,
    _: AuthenticatedUser = Depends(require_operator),
    service: BotCommonConfigServiceProtocol = Injected(BotCommonConfigServiceProtocol),
):
    ids = service.batch_upsert_records(
        env=env_utils.get_current_env(),
        records=[
            BotCommonConfigEntry(
                bot_id=item.bot_id,
                entity_id=item.entity_id,
                config_key=item.config_key,
                value=item.config_value,
            )
            for item in request.items
        ],
    )
    return {
        "success": True,
        "message": "配置已批量保存",
        "error_code": 200,
        "data": {"config_ids": ids},
    }


@router.post("/delete")
async def delete_bot_common_config(
    request: DeleteBotCommonConfigRequest,
    _: AuthenticatedUser = Depends(require_operator),
    service: BotCommonConfigServiceProtocol = Injected(BotCommonConfigServiceProtocol),
):
    if not service.delete_record(config_id=request.id):
        raise HTTPException(status_code=404, detail="配置不存在")
    return {"success": True, "message": "配置已删除", "error_code": 200, "data": None}
