from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from agentclaw.community.adapters.http.auth.dependencies import require_operator
from agentclaw.community.adapters.http.auth.models import AuthenticatedUser
from agentclaw.community.adapters.http.bot_common_config.schemas import (
    BatchUpsertBotCommonConfigRequest,
    CreateBotCommonConfigRequest,
    GetBotCommonConfigRequest,
    DeleteBotCommonConfigRequest,
    UpdateBotCommonConfigRequest,
)
from agentclaw.community.core.common_config.models import BotCommonConfigRecord
from agentclaw.community.core.common_config.bot_config_protocol import BotCommonConfigServiceProtocol
from agentclaw.community.di import Injected
from agentclaw.community.utils import env_utils

router = APIRouter(prefix="/api/v1/bot-common-config", tags=["bot-common-config"])


def _serialize(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value


def _parse_value(value: str) -> Any:
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return value


def _to_dict(record: BotCommonConfigRecord) -> dict[str, Any]:
    try:
        value = json.loads(record.config_value)
    except (json.JSONDecodeError, TypeError):
        value = record.config_value
    return {
        "id": record.id,
        "bot_id": record.bot_id,
        "entity_id": record.entity_id,
        "env": record.env,
        "config_key": record.config_key,
        "config_value": value,
        "is_delete": record.is_delete,
        "gmt_create": record.gmt_create.isoformat() if record.gmt_create else None,
        "gmt_modified": record.gmt_modified.isoformat() if record.gmt_modified else None,
    }


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
        bot_id=bot_id, entity_id=entity_id, env=env_utils.get_current_env(), config_key=config_key,
        page_num=page_num, page_size=page_size,
    )
    return {"success": True, "message": "OK", "error_code": 200,
            "data": {"total": total, "items": [_to_dict(r) for r in records]}}


@router.post("/get")
async def get_bot_common_config(
    request: GetBotCommonConfigRequest,
    _: AuthenticatedUser = Depends(require_operator),
    service: BotCommonConfigServiceProtocol = Injected(BotCommonConfigServiceProtocol),
):
    value = service.get(**request.model_dump(), env=env_utils.get_current_env())
    if value is None:
        raise HTTPException(status_code=404, detail="配置不存在")
    return {"success": True, "message": "OK", "error_code": 200,
            "data": {**request.model_dump(), "config_value": _parse_value(value)}}


@router.post("/create")
async def create_bot_common_config(
    request: CreateBotCommonConfigRequest,
    _: AuthenticatedUser = Depends(require_operator),
    service: BotCommonConfigServiceProtocol = Injected(BotCommonConfigServiceProtocol),
):
    try:
        config_id = service.create_record(
            **request.model_dump(exclude={"config_value"}),
            env=env_utils.get_current_env(),
            config_value=_serialize(request.config_value),
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail="配置创建失败") from exc
    return {"success": True, "message": "配置已创建", "error_code": 200, "data": {"config_id": config_id}}


@router.post("/update")
async def update_bot_common_config(
    request: UpdateBotCommonConfigRequest,
    _: AuthenticatedUser = Depends(require_operator),
    service: BotCommonConfigServiceProtocol = Injected(BotCommonConfigServiceProtocol),
):
    if not service.update_record(config_id=request.id, config_value=_serialize(request.config_value)):
        raise HTTPException(status_code=404, detail="配置不存在")
    return {"success": True, "message": "配置已更新", "error_code": 200, "data": None}


@router.post("/upsert")
async def upsert_bot_common_config(
    request: CreateBotCommonConfigRequest,
    _: AuthenticatedUser = Depends(require_operator),
    service: BotCommonConfigServiceProtocol = Injected(BotCommonConfigServiceProtocol),
):
    config_id = service.upsert_record(
        **request.model_dump(exclude={"config_value"}),
        env=env_utils.get_current_env(),
        config_value=_serialize(request.config_value),
    )
    return {"success": True, "message": "配置已保存", "error_code": 200, "data": {"config_id": config_id}}


@router.post("/batch-upsert")
async def batch_upsert_bot_common_configs(
    request: BatchUpsertBotCommonConfigRequest,
    _: AuthenticatedUser = Depends(require_operator),
    service: BotCommonConfigServiceProtocol = Injected(BotCommonConfigServiceProtocol),
):
    env = env_utils.get_current_env()
    records = [
        item.model_dump(exclude={"config_value"})
        | {"env": env, "config_value": _serialize(item.config_value)}
        for item in request.items
    ]
    ids = service.batch_upsert_records(records=records)
    return {"success": True, "message": "配置已批量保存", "error_code": 200, "data": {"config_ids": ids}}


@router.post("/delete")
async def delete_bot_common_config(
    request: DeleteBotCommonConfigRequest,
    _: AuthenticatedUser = Depends(require_operator),
    service: BotCommonConfigServiceProtocol = Injected(BotCommonConfigServiceProtocol),
):
    if not service.delete_record(config_id=request.id):
        raise HTTPException(status_code=404, detail="配置不存在")
    return {"success": True, "message": "配置已删除", "error_code": 200, "data": None}
