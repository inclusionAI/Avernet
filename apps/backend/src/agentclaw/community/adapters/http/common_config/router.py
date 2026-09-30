"""Common Config API Router."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from agentclaw.community.adapters.http.auth.dependencies import get_current_user, require_operator
from agentclaw.community.adapters.http.auth.models import AuthenticatedUser
from agentclaw.community.adapters.http.common_config.schemas import (
    ApiResponse,
    CreateCommonConfigRequest,
    DeleteCommonConfigRequest,
    GetCommonConfigRequest,
    GetCommonConfigValueRequest,
    ToggleCommonConfigRequest,
    UpdateCommonConfigRequest,
)
from agentclaw.community.api.common_config_service import CommonConfigServiceProtocol
from agentclaw.community.di import Injected
from agentclaw.community.utils import env_utils


router = APIRouter(prefix="/api/v1/common-config", tags=["common-config"])


@router.get("/list", response_model=ApiResponse[dict])
async def list_common_configs(
    business_code: str | None = None,
    enable: str | None = None,
    keyword: str | None = None,
    page_num: int = 1,
    page_size: int = 100,
    user: AuthenticatedUser = Depends(get_current_user),
    service: CommonConfigServiceProtocol = Injected(CommonConfigServiceProtocol),
):
    """分页查询通用配置。"""
    data = service.list_configs(
        env=env_utils.get_current_env(),
        business_code=business_code,
        enable=enable,
        keyword=keyword,
        page_num=page_num,
        page_size=page_size,
    )
    return ApiResponse(success=True, message="OK", error_code=200, data=data)


@router.post("/get", response_model=ApiResponse[dict])
async def get_common_config(
    req: GetCommonConfigRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    service: CommonConfigServiceProtocol = Injected(CommonConfigServiceProtocol),
):
    """按 business_code + param_code 查询配置详情。"""
    data = service.get_config(
        business_code=req.business_code,
        param_code=req.param_code,
        env=env_utils.get_current_env(),
        only_enabled=req.only_enabled,
    )
    if data is None:
        return ApiResponse(success=False, message="配置不存在", error_code=40401, data=None)
    return ApiResponse(success=True, message="OK", error_code=200, data=data)


@router.post("/value", response_model=ApiResponse[dict])
async def get_common_config_value(
    req: GetCommonConfigValueRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    service: CommonConfigServiceProtocol = Injected(CommonConfigServiceProtocol),
):
    """读取配置值，默认只返回启用配置。"""
    value = service.get_value(
        business_code=req.business_code,
        param_code=req.param_code,
        env=env_utils.get_current_env(),
        default=req.default,
        only_enabled=req.only_enabled,
    )
    return ApiResponse(
        success=True,
        message="OK",
        error_code=200,
        data={
            "business_code": req.business_code,
            "param_code": req.param_code,
            "value": value,
        },
    )


@router.post("/create", response_model=ApiResponse[dict])
async def create_common_config(
    req: CreateCommonConfigRequest,
    user: AuthenticatedUser = Depends(require_operator),
    service: CommonConfigServiceProtocol = Injected(CommonConfigServiceProtocol),
):
    """创建通用配置。"""
    try:
        config_id = service.create_config(
            business_code=req.business_code,
            business_name=req.business_name,
            param_code=req.param_code,
            param_name=req.param_name,
            param_value=req.param_value,
            enable=req.enable,
            ext_info=req.ext_info,
            env=env_utils.get_current_env(),
        )
    except ValueError as exc:
        return ApiResponse(success=False, message=str(exc), error_code=40001, data=None)
    return ApiResponse(
        success=True,
        message="配置已创建",
        error_code=200,
        data={"config_id": config_id},
    )


@router.post("/update", response_model=ApiResponse[dict])
async def update_common_config(
    req: UpdateCommonConfigRequest,
    user: AuthenticatedUser = Depends(require_operator),
    service: CommonConfigServiceProtocol = Injected(CommonConfigServiceProtocol),
):
    """更新通用配置。唯一键字段不允许通过此接口修改。"""
    payload = req.model_dump(exclude_unset=True)
    config_id = payload.pop("id")
    updates: dict[str, Any] = payload
    try:
        success = service.update_config(config_id=config_id, updates=updates)
    except ValueError as exc:
        return ApiResponse(success=False, message=str(exc), error_code=40001, data=None)
    if not success:
        return ApiResponse(success=False, message="配置不存在或无可更新字段", error_code=40401, data=None)
    return ApiResponse(success=True, message="配置已更新", error_code=200, data=None)


@router.post("/upsert", response_model=ApiResponse[dict])
async def upsert_common_config(
    req: CreateCommonConfigRequest,
    user: AuthenticatedUser = Depends(require_operator),
    service: CommonConfigServiceProtocol = Injected(CommonConfigServiceProtocol),
):
    """按 business_code + param_code + 当前环境创建或更新配置。"""
    try:
        config_id = service.upsert_config(
            business_code=req.business_code,
            business_name=req.business_name,
            param_code=req.param_code,
            param_name=req.param_name,
            param_value=req.param_value,
            enable=req.enable,
            ext_info=req.ext_info,
            env=env_utils.get_current_env(),
        )
    except ValueError as exc:
        return ApiResponse(success=False, message=str(exc), error_code=40001, data=None)
    return ApiResponse(
        success=True,
        message="配置已保存",
        error_code=200,
        data={"config_id": config_id},
    )


@router.post("/delete", response_model=ApiResponse[dict])
async def delete_common_config(
    req: DeleteCommonConfigRequest,
    user: AuthenticatedUser = Depends(require_operator),
    service: CommonConfigServiceProtocol = Injected(CommonConfigServiceProtocol),
):
    """删除通用配置，支持按 ID 或按唯一键删除。"""
    try:
        success = service.delete_config(
            config_id=req.id,
            business_code=req.business_code,
            param_code=req.param_code,
            env=env_utils.get_current_env() if req.id is None else None,
        )
    except ValueError as exc:
        return ApiResponse(success=False, message=str(exc), error_code=40001, data=None)
    if not success:
        return ApiResponse(success=False, message="配置不存在", error_code=40401, data=None)
    return ApiResponse(success=True, message="配置已删除", error_code=200, data=None)


@router.post("/enable", response_model=ApiResponse[dict])
async def enable_common_config(
    req: ToggleCommonConfigRequest,
    user: AuthenticatedUser = Depends(require_operator),
    service: CommonConfigServiceProtocol = Injected(CommonConfigServiceProtocol),
):
    try:
        success = service.enable_config(config_id=req.id)
    except ValueError as exc:
        return ApiResponse(success=False, message=str(exc), error_code=40001, data=None)
    if not success:
        return ApiResponse(success=False, message="配置不存在", error_code=40401, data=None)
    return ApiResponse(success=True, message="配置已启用", error_code=200, data=None)


@router.post("/disable", response_model=ApiResponse[dict])
async def disable_common_config(
    req: ToggleCommonConfigRequest,
    user: AuthenticatedUser = Depends(require_operator),
    service: CommonConfigServiceProtocol = Injected(CommonConfigServiceProtocol),
):
    try:
        success = service.disable_config(config_id=req.id)
    except ValueError as exc:
        return ApiResponse(success=False, message=str(exc), error_code=40001, data=None)
    if not success:
        return ApiResponse(success=False, message="配置不存在", error_code=40401, data=None)
    return ApiResponse(success=True, message="配置已禁用", error_code=200, data=None)
