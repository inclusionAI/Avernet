"""System Config API Router — FastAPI endpoint definitions.

This module defines the HTTP endpoints for system configuration management.
It only handles HTTP concerns: path/query/body parsing and response formatting.

Business logic is delegated to SystemConfigService and DeviceConfigService.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from agentclaw.community.adapters.http.system_config.schemas import (
    AddAllocationListRequest,
    ApiResponse,
    CreateCategoryRequest,
    DeleteCategoryRequest,
    DeleteConfigRequest,
    GetConfigRequest,
    RemoveAllocationListRequest,
    SetConfigRequest,
    SetDefaultProviderRequest,
    SetPersonalBotBaasDisableRequest,
    SetTemplateTypeProviderMapRequest,
)
from agentclaw.community.adapters.http.auth.models import AuthenticatedUser
from agentclaw.community.adapters.http.auth.dependencies import get_current_user, require_operator
from agentclaw.community.api.device_config_service import DeviceConfigServiceProtocol
from agentclaw.community.api.system_config_service import SystemConfigServiceProtocol
from agentclaw.community.di import Injected
from agentclaw.community.utils import env_utils
from agentclaw.community.log import get_logger


def get_operator(user: AuthenticatedUser) -> str:
    """Identify the actor for audit fields on writes."""
    return user.operatorName or user.staffId


logger = get_logger()
router = APIRouter(prefix="/api/v1/config", tags=["system-config"])


# ============================================================
# 分类目录 API
# ============================================================


@router.get("/categories", response_model=ApiResponse[list])
async def list_categories(
    user: AuthenticatedUser = Depends(get_current_user),
    service: SystemConfigServiceProtocol = Injected(SystemConfigServiceProtocol),
):
    """列出所有分类目录"""
    env = env_utils.get_current_env()
    categories = service.list_categories(env=env)
    return ApiResponse(success=True, message="OK", error_code=200, data=categories)


@router.get("/categories/{category_id}", response_model=ApiResponse[dict])
async def get_category(
    category_id: int,
    user: AuthenticatedUser = Depends(get_current_user),
    service: SystemConfigServiceProtocol = Injected(SystemConfigServiceProtocol),
):
    """获取分类目录详情"""
    cat = service.get_category_by_id(category_id=category_id)
    if cat is None:
        return ApiResponse(success=False, message="分类不存在", error_code=40401, data=None)
    return ApiResponse(success=True, message="OK", error_code=200, data=cat)


@router.post("/categories", response_model=ApiResponse[dict])
async def create_category(
    req: CreateCategoryRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    service: SystemConfigServiceProtocol = Injected(SystemConfigServiceProtocol),
):
    """创建分类目录"""
    env = env_utils.get_current_env()
    operator = get_operator(user)
    category_id = service.create_category(
        category=req.category,
        category_name=req.category_name,
        description=req.description,
        env=env,
        operator=operator,
    )
    return ApiResponse(
        success=True,
        message="分类目录已创建",
        error_code=200,
        data={"category_id": category_id},
    )


@router.post("/categories/delete", response_model=ApiResponse[dict])
async def delete_category(
    req: DeleteCategoryRequest,
    user: AuthenticatedUser = Depends(require_operator),
    service: SystemConfigServiceProtocol = Injected(SystemConfigServiceProtocol),
):
    """删除分类目录（级联删除配置项）"""
    success = service.delete_category(category_id=req.category_id)
    if success:
        return ApiResponse(success=True, message="分类目录已删除", error_code=200, data=None)
    return ApiResponse(success=False, message="分类目录不存在", error_code=40401, data=None)


# ============================================================
# 配置项 API
# ============================================================


@router.get("/list", response_model=ApiResponse[list])
async def list_configs(
    category: str | None = None,
    user: AuthenticatedUser = Depends(get_current_user),
    service: SystemConfigServiceProtocol = Injected(SystemConfigServiceProtocol),
):
    """列出配置项"""
    env = env_utils.get_current_env()
    if category:
        configs = service.list_configs(category=category, env=env)
    else:
        configs = service.list_all_configs(env=env)
    return ApiResponse(success=True, message="OK", error_code=200, data=configs)


@router.post("/get", response_model=ApiResponse[dict])
async def get_config(
    req: GetConfigRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    service: SystemConfigServiceProtocol = Injected(SystemConfigServiceProtocol),
):
    """获取配置值"""
    env = env_utils.get_current_env()
    value = service.get_config(category=req.category, config_key=req.config_key, env=env)
    if value is None:
        return ApiResponse(success=False, message="配置不存在", error_code=40401, data=None)
    return ApiResponse(
        success=True,
        message="OK",
        error_code=200,
        data={"category": req.category, "config_key": req.config_key, "value": value},
    )


@router.post("/set", response_model=ApiResponse[dict])
async def set_config(
    req: SetConfigRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    service: SystemConfigServiceProtocol = Injected(SystemConfigServiceProtocol),
):
    """设置配置值（分类不存在会自动创建）"""
    env = env_utils.get_current_env()
    operator = get_operator(user)
    config_id = service.set_config(
        category=req.category,
        config_key=req.config_key,
        config_value=req.config_value,
        env=env,
        description=req.description,
        operator=operator,
    )
    return ApiResponse(
        success=True,
        message="配置已保存",
        error_code=200,
        data={"config_id": config_id},
    )


@router.post("/delete", response_model=ApiResponse[dict])
async def delete_config(
    req: DeleteConfigRequest,
    user: AuthenticatedUser = Depends(require_operator),
    service: SystemConfigServiceProtocol = Injected(SystemConfigServiceProtocol),
):
    """删除配置"""
    env = env_utils.get_current_env()
    success = service.delete_config_by_key(
        category=req.category, config_key=req.config_key, env=env
    )
    if success:
        return ApiResponse(success=True, message="配置已删除", error_code=200, data=None)
    return ApiResponse(success=False, message="配置不存在", error_code=40401, data=None)


# ============================================================
# 设备配置 API（使用 DeviceConfigService）
# ============================================================


@router.get("/device/allocation", response_model=ApiResponse[dict])
async def get_allocation_lists(
    user: AuthenticatedUser = Depends(get_current_user),
    service: DeviceConfigServiceProtocol = Injected(DeviceConfigServiceProtocol),
):
    """获取设备分配名单"""
    env = env_utils.get_current_env()
    lists = service.get_allocation_lists(env=env)
    return ApiResponse(success=True, message="OK", error_code=200, data=lists)


@router.post("/device/allocation/add", response_model=ApiResponse[dict])
async def add_to_allocation_list(
    req: AddAllocationListRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    service: DeviceConfigServiceProtocol = Injected(DeviceConfigServiceProtocol),
):
    """添加员工到设备分配名单"""
    env = env_utils.get_current_env()
    creator = get_operator(user)
    try:
        added_count = service.add_to_allocation_list(
            staff_ids=req.staff_ids,
            provider=req.provider,
            env=env,
            creator=creator,
        )
    except ValueError as e:
        return ApiResponse(success=False, message=str(e), error_code=40001, data=None)
    return ApiResponse(
        success=True,
        message=f"已添加 {added_count} 名员工到 {req.provider} 分配名单",
        error_code=200,
        data={"added_count": added_count},
    )


@router.post("/device/allocation/remove", response_model=ApiResponse[dict])
async def remove_from_allocation_list(
    req: RemoveAllocationListRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    service: DeviceConfigServiceProtocol = Injected(DeviceConfigServiceProtocol),
):
    """从设备分配名单中移除员工"""
    env = env_utils.get_current_env()
    creator = get_operator(user)
    try:
        removed_count = service.remove_from_allocation_list(
            staff_ids=req.staff_ids,
            provider=req.provider,
            env=env,
            creator=creator,
        )
    except ValueError as e:
        return ApiResponse(success=False, message=str(e), error_code=40001, data=None)
    return ApiResponse(
        success=True,
        message=f"已从 {req.provider} 分配名单中移除 {removed_count} 名员工",
        error_code=200,
        data={"removed_count": removed_count},
    )


@router.post("/device/default-provider", response_model=ApiResponse[dict])
async def set_default_provider(
    req: SetDefaultProviderRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    service: DeviceConfigServiceProtocol = Injected(DeviceConfigServiceProtocol),
):
    """设置默认设备 Provider"""
    env = env_utils.get_current_env()
    creator = get_operator(user)
    try:
        service.set_default_provider(provider=req.provider, env=env, creator=creator)
    except ValueError as e:
        return ApiResponse(success=False, message=str(e), error_code=40001, data=None)
    return ApiResponse(
        success=True,
        message=f"默认设备 Provider 已设置为 {req.provider}",
        error_code=200,
        data=None,
    )


@router.get("/device/max-devices", response_model=ApiResponse[dict])
async def get_max_devices(
    user: AuthenticatedUser = Depends(get_current_user),
    service: DeviceConfigServiceProtocol = Injected(DeviceConfigServiceProtocol),
):
    """获取每个实体最大设备数量"""
    env = env_utils.get_current_env()
    max_devices = service.get_max_devices(env=env)
    return ApiResponse(success=True, message="OK", error_code=200, data={"max_devices": max_devices})


@router.get("/device/allocation-mode", response_model=ApiResponse[dict])
async def get_allocation_mode(
    user: AuthenticatedUser = Depends(get_current_user),
    service: DeviceConfigServiceProtocol = Injected(DeviceConfigServiceProtocol),
):
    """获取设备分配模式"""
    env = env_utils.get_current_env()
    mode = service.get_allocation_mode(env=env)
    return ApiResponse(success=True, message="OK", error_code=200, data={"allocation_mode": mode})


@router.post("/device/template-type-provider-map", response_model=ApiResponse[dict])
async def set_template_type_provider_map(
    req: SetTemplateTypeProviderMapRequest,
    user: AuthenticatedUser = Depends(require_operator),
    service: DeviceConfigServiceProtocol = Injected(DeviceConfigServiceProtocol),
):
    """设置 template_type → provider 映射（灰度个人 Bot 走 BaaS）"""
    env = env_utils.get_current_env()
    creator = get_operator(user)
    try:
        service.set_template_type_provider_map(mapping=req.mapping, env=env, creator=creator)
    except ValueError as e:
        return ApiResponse(success=False, message=str(e), error_code=40001, data=None)
    return ApiResponse(
        success=True,
        message="template_type → provider 映射已更新",
        error_code=200,
        data={"mapping": req.mapping},
    )


@router.post("/device/personal-bot-baas-disable", response_model=ApiResponse[dict])
async def set_personal_bot_baas_disable(
    req: SetPersonalBotBaasDisableRequest,
    user: AuthenticatedUser = Depends(require_operator),
    service: DeviceConfigServiceProtocol = Injected(DeviceConfigServiceProtocol),
):
    """紧急回退开关：true 时所有 personal bot 强制走 arca"""
    env = env_utils.get_current_env()
    creator = get_operator(user)
    service.set_personal_bot_baas_disable(disabled=req.disabled, env=env, creator=creator)
    return ApiResponse(
        success=True,
        message=f"personal_bot_baas_disable 已设置为 {req.disabled}",
        error_code=200,
        data={"disabled": req.disabled},
    )
