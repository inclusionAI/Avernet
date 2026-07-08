"""
API Key 管理员 RESTful API 路由

提供 API Key 的管理员管理接口
"""

from typing import Any, Literal

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from secbaas.adapters.web.dependencies import get_op_ctx
from secbaas.api import ApiResponse, OperationContext
from secbaas.api.api_gateway import (
    APIKeyCreate,
    APIKeyError,
    APIKeyQuery,
    APIKeyService,
    APIKeyStatus,
    APIKeyUpdate,
    is_admin,
    parse_bot_entity_id,
    parse_policy,
)
from secbaas.bootstrap import ApplicationContainer
from secbaas.logger import get_logger

router = APIRouter(prefix="/api/v1/admin/api-keys", tags=["API Key 管理"])

logger = get_logger("router-admin")

# ==================== Request Models ====================


class AdminCreateAPIKeyRequest(BaseModel):
    """管理员创建 API Key 请求"""

    app_type: str = Field(..., max_length=64, description="应用类型（app/bot）")
    app_id: str = Field(..., min_length=1, max_length=128, description="应用ID")
    key_name: str | None = Field(None, max_length=128, description="密钥名称")
    description: str | None = Field(None, description="描述")
    rate_limit_rpm: int | None = Field(None, description="每分钟请求数限制")
    rate_limit_rpd: int | None = Field(None, description="每天请求数限制")
    owner: str | None = Field(None, max_length=64, description="Owner")
    tenant: str | None = Field(None, max_length=64, description="租户标识")
    policy: str | None = Field(None, description="权限策略，JSON 格式")


class UpdateAPIKeyConfigRequest(BaseModel):
    """更新 API Key 配置请求（仅管理员可调用）"""

    key_name: str | None = Field(None, max_length=128, description="密钥名称")
    description: str | None = Field(None, description="描述")
    app_id: str | None = Field(None, min_length=1, max_length=128, description="应用ID")
    app_type: str | None = Field(None, max_length=64, description="应用类型")
    rate_limit_rpm: int | None = Field(None, description="每分钟请求数限制")
    rate_limit_rpd: int | None = Field(None, description="每天请求数限制")
    owner: str | None = Field(None, max_length=64, description="Owner")
    tenant: str | None = Field(None, max_length=64, description="租户标识")
    policy: str | None = Field(None, description="权限策略，JSON 格式")


class AdminStatusActionRequest(BaseModel):
    """管理员状态操作请求"""

    action: Literal["activate", "deactivate", "revoke"] = Field(
        ..., description="操作类型：activate 启用，deactivate 停用，revoke 吊销"
    )


class AdminBotIdRequest(BaseModel):
    """管理员 Bot ID 请求（grant/revoke allowed_bots）"""

    bot_id: str = Field(
        ...,
        min_length=1,
        max_length=128,
        description="Bot ID，格式为 real_bot_id:entity_id",
    )


# ==================== Dependencies ====================


async def _require_admin(
    op_ctx: OperationContext = Depends(get_op_ctx),
) -> OperationContext:
    """校验管理员权限"""
    if not is_admin(op_ctx.operator):
        raise HTTPException(status_code=403, detail="无权限执行此操作")
    return op_ctx


# ==================== 管理员接口 ====================


@router.get(
    "",
    summary="查询 API Key 列表（管理员）",
    description="分页查询 API Key 列表，支持多种筛选条件，仅管理员可调用",
    response_model=ApiResponse,
)
@inject
async def list_all_keys(
    app_id: str | None = Query(None, description="应用ID筛选"),
    app_type: str | None = Query(None, description="应用类型筛选"),
    status: APIKeyStatus | None = Query(None, description="状态筛选"),
    creator: str | None = Query(None, description="创建人筛选"),
    owner: str | None = Query(None, description="Owner 筛选"),
    tenant: str | None = Query(None, description="租户标识筛选"),
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页数量"),
    _op_ctx: OperationContext = Depends(_require_admin),
    service: APIKeyService = Depends(
        Provide[ApplicationContainer.services.api_key_service]
    ),
) -> ApiResponse[Any]:
    """查询 API Key 列表，仅管理员可调用"""
    query = APIKeyQuery(
        app_id=app_id,
        app_type=app_type,
        status=status,
        creator=creator,
        owner=owner,
        tenant=tenant,
    )
    result = await service.list_keys(
        query=query,
        ctx=_op_ctx,
        page=page,
        page_size=page_size,
    )
    return ApiResponse(data=result)


@router.post(
    "",
    summary="创建 API Key（管理员）",
    description="管理员创建任意类型的 API Key",
    response_model=ApiResponse,
    responses={
        200: {"description": "API Key 创建成功"},
        403: {"description": "无权限执行此操作"},
        400: {"description": "请求参数错误"},
        500: {"description": "服务器内部错误"},
    },
)
@inject
async def create_key_admin(
    data: AdminCreateAPIKeyRequest,
    _op_ctx: OperationContext = Depends(_require_admin),
    service: APIKeyService = Depends(
        Provide[ApplicationContainer.services.api_key_service]
    ),
) -> ApiResponse[Any]:
    """管理员创建 API Key"""
    create_data = APIKeyCreate(
        app_type=data.app_type,
        app_id=data.app_id,
        key_name=data.key_name,
        description=data.description,
        rate_limit_rpm=data.rate_limit_rpm,
        rate_limit_rpd=data.rate_limit_rpd,
        owner=data.owner,
        tenant=data.tenant,
        policy=data.policy,
    )
    try:
        key = await service.create_key(create_data, _op_ctx)
        logger.info(
            f"Audit: create_key_admin operator={_op_ctx.operator} "
            f"app_type={data.app_type} app_id={data.app_id} prefix={key.api_key_prefix}"
        )
        return ApiResponse(data=key)
    except APIKeyError as e:
        raise HTTPException(status_code=400, detail=e.message)
    except Exception:
        logger.exception("创建 API Key 失败（管理员）")
        raise HTTPException(status_code=500, detail="创建 API Key 失败")


@router.put(
    "/{api_key_prefix}/config",
    summary="更新 API Key 配置",
    description="更新 API Key 的配置（key_name, description, app_id, app_type, rate_limit, owner, tenant, policy），仅管理员可执行",
    response_model=ApiResponse,
    responses={
        200: {"description": "更新成功"},
        403: {"description": "无权限执行此操作"},
        404: {"description": "API Key 不存在"},
        400: {"description": "请求参数错误"},
    },
)
@inject
async def update_api_key_config(
    api_key_prefix: str,
    data: UpdateAPIKeyConfigRequest,
    _op_ctx: OperationContext = Depends(_require_admin),
    service: APIKeyService = Depends(
        Provide[ApplicationContainer.services.api_key_service]
    ),
) -> ApiResponse[Any]:
    """更新 API Key 配置，仅管理员可调用"""
    # 校验参数：至少提供一个
    has_any_field = any(
        [
            data.key_name is not None,
            data.description is not None,
            data.app_id is not None,
            data.app_type is not None,
            data.rate_limit_rpm is not None,
            data.rate_limit_rpd is not None,
            data.owner is not None,
            data.tenant is not None,
            data.policy is not None,
        ]
    )
    if not has_any_field:
        raise HTTPException(status_code=400, detail="至少需要一个更新字段")

    # 查询 API Key 是否存在
    api_key = await service.get_key_by_prefix(api_key_prefix, _op_ctx)
    if not api_key:
        raise HTTPException(status_code=404, detail="API Key 不存在")

    # 构建更新数据
    update_data = APIKeyUpdate(
        key_name=data.key_name,
        description=data.description,
        app_id=data.app_id,
        app_type=data.app_type,
        rate_limit_rpm=data.rate_limit_rpm,
        rate_limit_rpd=data.rate_limit_rpd,
        owner=data.owner,
        tenant=data.tenant,
        policy=data.policy,
    )

    key = await service.update_key_by_prefix(api_key_prefix, update_data, _op_ctx)

    logger.info(
        f"Audit: update_key_config operator={_op_ctx.operator} prefix={api_key_prefix}"
    )
    return ApiResponse(data=key, message="API Key 配置已更新")


@router.get(
    "/{api_key_prefix}",
    summary="查询 API Key 详情（管理员）",
    description="根据 API Key 前缀查询详情，仅管理员可调用",
    response_model=ApiResponse,
    responses={
        200: {"description": "查询成功"},
        403: {"description": "无权限执行此操作"},
        404: {"description": "API Key 不存在"},
    },
)
@inject
async def get_key_admin(
    api_key_prefix: str,
    _op_ctx: OperationContext = Depends(_require_admin),
    service: APIKeyService = Depends(
        Provide[ApplicationContainer.services.api_key_service]
    ),
) -> ApiResponse[Any]:
    """管理员查询 API Key 详情"""
    api_key = await service.get_key_by_prefix(api_key_prefix, _op_ctx)
    if not api_key:
        raise HTTPException(status_code=404, detail="API Key 不存在")
    return ApiResponse(data=api_key)


@router.patch(
    "/{api_key_prefix}/status",
    summary="更新 API Key 状态（管理员）",
    description="管理员启用、停用或吊销 API Key",
    response_model=ApiResponse,
    responses={
        200: {"description": "操作成功"},
        403: {"description": "无权限执行此操作"},
        404: {"description": "API Key 不存在"},
        400: {"description": "状态不允许此操作"},
    },
)
@inject
async def update_key_status_admin(
    api_key_prefix: str,
    data: AdminStatusActionRequest,
    _op_ctx: OperationContext = Depends(_require_admin),
    service: APIKeyService = Depends(
        Provide[ApplicationContainer.services.api_key_service]
    ),
) -> ApiResponse[Any]:
    """管理员更新 API Key 状态"""
    try:
        if data.action == "activate":
            key = await service.activate_by_prefix(api_key_prefix, _op_ctx)
            message = "API Key 已启用"
        elif data.action == "deactivate":
            key = await service.deactivate_by_prefix(api_key_prefix, _op_ctx)
            message = "API Key 已停用"
        else:  # revoke
            key = await service.revoke_by_prefix(api_key_prefix, _op_ctx)
            message = "API Key 已吊销"

        if not key:
            raise HTTPException(status_code=404, detail="API Key 不存在")
        logger.info(
            f"Audit: update_key_status_admin operator={_op_ctx.operator} "
            f"prefix={api_key_prefix} action={data.action}"
        )
        return ApiResponse(data=key, message=message)
    except APIKeyError as e:
        raise HTTPException(status_code=400, detail=e.message)


# ==================== allowed-bots 管理接口 ====================


@router.get(
    "/{api_key_prefix}/allowed-bots",
    summary="查询 allowed_bots（管理员）",
    description="获取 app_type=app 的 API Key 的 policy 中的 allowed_bots 列表，仅管理员可调用",
    response_model=ApiResponse,
    responses={
        200: {"description": "查询成功"},
        400: {"description": "非 app 类型 Key"},
        403: {"description": "无权限执行此操作"},
        404: {"description": "API Key 不存在"},
    },
)
@inject
async def get_allowed_bots_admin(
    api_key_prefix: str,
    _op_ctx: OperationContext = Depends(_require_admin),
    service: APIKeyService = Depends(
        Provide[ApplicationContainer.services.api_key_service]
    ),
) -> ApiResponse[Any]:
    """管理员获取 API Key 的 allowed_bots 列表（仅 app_type=app）"""
    api_key = await service.get_key_by_prefix(api_key_prefix, _op_ctx)
    if not api_key:
        raise HTTPException(status_code=404, detail="API Key 不存在")

    if api_key.app_type != "app":
        raise HTTPException(
            status_code=400, detail="仅 app_type=app 的 API Key 支持 allowed_bots 操作"
        )

    policy = parse_policy(api_key.policy)
    # 过滤 NONE 哨兵值，对外返回语义为空列表
    bots = [b for b in policy.allowed_bots if b != "NONE"]
    logger.info(
        f"Audit: get_allowed_bots_admin operator={_op_ctx.operator} "
        f"prefix={api_key_prefix} count={len(bots)}"
    )
    return ApiResponse(data={"allowed_bots": bots})


@router.post(
    "/{api_key_prefix}/allowed-bots/grant",
    summary="授权 bot 访问（管理员）",
    description="管理员向 app_type=app 的 API Key 授权一个 bot 访问权限，无需 bot owner 校验",
    response_model=ApiResponse,
    responses={
        200: {"description": "授权成功"},
        400: {"description": "请求参数错误或非 app 类型 Key"},
        403: {"description": "无权限执行此操作"},
        404: {"description": "API Key 不存在"},
    },
)
@inject
async def grant_allowed_bot_admin(
    api_key_prefix: str,
    data: AdminBotIdRequest,
    _op_ctx: OperationContext = Depends(_require_admin),
    service: APIKeyService = Depends(
        Provide[ApplicationContainer.services.api_key_service]
    ),
) -> ApiResponse[Any]:
    """管理员授权 bot 访问（仅 app_type=app），无需 bot owner 校验"""
    api_key = await service.get_key_by_prefix(api_key_prefix, _op_ctx)
    if not api_key:
        raise HTTPException(status_code=404, detail="API Key 不存在")

    if api_key.app_type != "app":
        raise HTTPException(
            status_code=400, detail="仅 app_type=app 的 API Key 支持 allowed_bots 操作"
        )

    # 验证 bot_id 格式必须为 real_bot_id:entity_id
    if parse_bot_entity_id(data.bot_id) is None:
        raise HTTPException(
            status_code=400,
            detail="bot_id 格式无效，必须为 real_bot_id:entity_id",
        )

    policy = parse_policy(api_key.policy)

    # 如果当前是 NONE 哨兵，清空后替换为实际 bot_id
    if policy.allowed_bots == ["NONE"]:
        policy.allowed_bots = []
    if data.bot_id not in set(policy.allowed_bots):
        policy.allowed_bots.append(data.bot_id)

    update_data = APIKeyUpdate(policy=policy.to_json())
    key = await service.update_key_by_prefix(api_key_prefix, update_data, _op_ctx)
    if not key:
        raise HTTPException(status_code=404, detail="API Key 不存在")
    logger.info(
        f"Audit: grant_allowed_bot_admin operator={_op_ctx.operator} "
        f"prefix={api_key_prefix} bot_id={data.bot_id}"
    )
    return ApiResponse(data=key, message="allowed_bots 已授权")


@router.post(
    "/{api_key_prefix}/allowed-bots/revoke",
    summary="撤销 bot 访问（管理员）",
    description="管理员撤销 app_type=app 的 API Key 中一个 bot 的访问权限，无需 bot owner 校验",
    response_model=ApiResponse,
    responses={
        200: {"description": "撤销成功"},
        400: {"description": "请求参数错误或非 app 类型 Key"},
        403: {"description": "无权限执行此操作"},
        404: {"description": "API Key 不存在"},
    },
)
@inject
async def revoke_allowed_bot_admin(
    api_key_prefix: str,
    data: AdminBotIdRequest,
    _op_ctx: OperationContext = Depends(_require_admin),
    service: APIKeyService = Depends(
        Provide[ApplicationContainer.services.api_key_service]
    ),
) -> ApiResponse[Any]:
    """管理员撤销 bot 访问（仅 app_type=app），无需 bot owner 校验"""
    api_key = await service.get_key_by_prefix(api_key_prefix, _op_ctx)
    if not api_key:
        raise HTTPException(status_code=404, detail="API Key 不存在")

    if api_key.app_type != "app":
        raise HTTPException(
            status_code=400, detail="仅 app_type=app 的 API Key 支持 allowed_bots 操作"
        )

    # 验证 bot_id 格式必须为 real_bot_id:entity_id
    if parse_bot_entity_id(data.bot_id) is None:
        raise HTTPException(
            status_code=400,
            detail="bot_id 格式无效，必须为 real_bot_id:entity_id",
        )

    policy = parse_policy(api_key.policy)

    if data.bot_id not in set(policy.allowed_bots):
        raise HTTPException(status_code=404, detail="bot_id 不在 allowed_bots 中")
    policy.allowed_bots = [b for b in policy.allowed_bots if b != data.bot_id]

    update_data = APIKeyUpdate(policy=policy.to_json())
    key = await service.update_key_by_prefix(api_key_prefix, update_data, _op_ctx)
    if not key:
        raise HTTPException(status_code=404, detail="API Key 不存在")
    logger.info(
        f"Audit: revoke_allowed_bot_admin operator={_op_ctx.operator} "
        f"prefix={api_key_prefix} bot_id={data.bot_id}"
    )
    return ApiResponse(data=key, message="allowed_bots 已撤销")
