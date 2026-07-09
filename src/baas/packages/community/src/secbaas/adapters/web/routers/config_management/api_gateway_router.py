"""
API Key RESTful API 路由

提供 API Key 的管理接口（普通用户）
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
    APIKeyResponse,
    APIKeyService,
    APIKeyStatus,
    APIKeyUpdate,
    AppAPIKeyCreate,
    BotAPIKeyCreate,
    check_bot_permission,
    check_permission,
    parse_bot_entity_id,
    parse_policy,
)
from secbaas.bootstrap import ApplicationContainer
from secbaas.logger import get_logger

router = APIRouter(prefix="/api/v1/api-keys", tags=["API Key 管理"])

logger = get_logger("router")

# ==================== Request Models ====================


class BotIdRequest(BaseModel):
    """Bot ID 请求（grant/revoke allowed_bots）"""

    bot_id: str = Field(
        ...,
        min_length=1,
        max_length=128,
        description="Bot ID，格式为 real_bot_id:entity_id",
    )


class UpdateAPIKeyRequest(BaseModel):
    """更新 API Key 元数据请求（仅允许 key_name 和 description）"""

    key_name: str | None = Field(None, max_length=128, description="密钥名称")
    description: str | None = Field(None, description="描述")


class StatusActionRequest(BaseModel):
    """状态操作请求"""

    action: Literal["activate", "deactivate", "revoke"] = Field(
        ..., description="操作类型：activate 启用，deactivate 停用，revoke 吊销"
    )


# ==================== Dependencies ====================


@inject
async def _check_api_key_permission(
    api_key_prefix: str,
    op_ctx: OperationContext = Depends(get_op_ctx),
    service: APIKeyService = Depends(
        Provide[ApplicationContainer.services.api_key_service]
    ),
) -> APIKeyResponse:
    """校验用户对 API Key 的操作权限（key owner 或 admin）"""
    operator = op_ctx.operator

    api_key = await service.get_key_by_prefix(api_key_prefix, op_ctx)
    if not api_key:
        raise HTTPException(status_code=404, detail="API Key 不存在")

    if not check_permission(operator, api_key):
        raise HTTPException(status_code=403, detail="无权限执行此操作")

    return api_key


@inject
async def _get_api_key_or_404(
    api_key_prefix: str,
    op_ctx: OperationContext = Depends(get_op_ctx),
    service: APIKeyService = Depends(
        Provide[ApplicationContainer.services.api_key_service]
    ),
) -> APIKeyResponse:
    """查询 API Key，不存在则 404（不做权限校验，由调用方自行检查）"""
    api_key = await service.get_key_by_prefix(api_key_prefix, op_ctx)
    if not api_key:
        raise HTTPException(status_code=404, detail="API Key 不存在")
    return api_key


# ==================== 创建接口 ====================


@router.post(
    "/bot",
    summary="创建 Bot API Key",
    description="创建一个 app_type 为 bot 的 API Key，自动设置 app_type=bot",
    response_model=ApiResponse,
    responses={
        200: {"description": "API Key 创建成功"},
        403: {"description": "无权限执行此操作"},
        400: {"description": "请求参数错误"},
        500: {"description": "服务器内部错误"},
    },
)
@inject
async def create_bot_key(
    data: BotAPIKeyCreate,
    op_ctx: OperationContext = Depends(get_op_ctx),
    service: APIKeyService = Depends(
        Provide[ApplicationContainer.services.api_key_service]
    ),
) -> ApiResponse[Any]:
    """创建 Bot API Key，权限校验：仅 owner（operator == entity_id）可创建"""
    operator = op_ctx.operator

    # 验证 app_id 格式必须为 real_bot_id:entity_id
    if parse_bot_entity_id(data.app_id) is None:
        raise HTTPException(
            status_code=400,
            detail="app_id 格式无效，必须为 real_bot_id:entity_id",
        )

    # 权限校验：仅 owner 可创建
    if not check_bot_permission(operator, data.app_id):
        raise HTTPException(status_code=403, detail="无权限执行此操作")

    create_data = APIKeyCreate(
        **data.model_dump(),
        app_type="bot",
        owner=operator,
    )
    try:
        key = await service.create_key(create_data, op_ctx)
        logger.info(
            f"Audit: create_bot_key operator={operator} app_id={data.app_id} prefix={key.api_key_prefix}"
        )
        return ApiResponse(data=key)
    except APIKeyError as e:
        raise HTTPException(status_code=400, detail=e.message)
    except Exception:
        logger.exception("创建 Bot API Key 失败")
        raise HTTPException(status_code=500, detail="创建 API Key 失败")


@router.post(
    "/app",
    summary="创建 App API Key",
    description='创建一个 app_type 为 app 的 API Key，自动设置 app_type=app。任何已认证用户均可创建，owner 自动填充为当前用户，policy 默认不允许访问任何 bot (allowed_bots=["NONE"])，后续通过 grant/revoke 接口管理',
    response_model=ApiResponse,
    responses={
        200: {"description": "API Key 创建成功"},
        400: {"description": "请求参数错误"},
        500: {"description": "服务器内部错误"},
    },
)
@inject
async def create_app_key(
    data: AppAPIKeyCreate,
    op_ctx: OperationContext = Depends(get_op_ctx),
    service: APIKeyService = Depends(
        Provide[ApplicationContainer.services.api_key_service]
    ),
) -> ApiResponse[Any]:
    """创建 App API Key，任何已认证用户可创建，owner 从当前用户自动填充，policy 默认不允许访问任何 bot"""
    operator = op_ctx.operator

    create_data = APIKeyCreate(
        **data.model_dump(),
        app_type="app",
        owner=operator,
        policy='{"allowed_bots":["NONE"]}',
    )
    try:
        key = await service.create_key(create_data, op_ctx)
        logger.info(
            f"Audit: create_app_key operator={operator} app_id={data.app_id} prefix={key.api_key_prefix}"
        )
        return ApiResponse(data=key)
    except APIKeyError as e:
        raise HTTPException(status_code=400, detail=e.message)
    except Exception:
        logger.exception("创建 App API Key 失败")
        raise HTTPException(status_code=500, detail="创建 API Key 失败")


# ==================== 查询接口 ====================


@router.get(
    "",
    summary="查询我的 API Key 列表",
    description="查询 owner 为当前用户的 API Key 列表，支持 app_type 筛选",
    response_model=ApiResponse,
)
@inject
async def list_my_keys(
    app_type: str | None = Query(None, description="应用类型筛选（bot/app）"),
    status: APIKeyStatus | None = Query(None, description="状态筛选"),
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页数量"),
    op_ctx: OperationContext = Depends(get_op_ctx),
    service: APIKeyService = Depends(
        Provide[ApplicationContainer.services.api_key_service]
    ),
) -> ApiResponse[Any]:
    """查询当前用户的 API Key 列表"""
    query = APIKeyQuery(
        app_type=app_type,
        owner=op_ctx.operator,
        status=status,
    )
    result = await service.list_keys(
        query=query,
        ctx=op_ctx,
        page=page,
        page_size=page_size,
    )
    return ApiResponse(data=result)


@router.get(
    "/{api_key_prefix}",
    summary="查询 API Key 详情",
    description="根据 API Key 前缀查询详情",
    response_model=ApiResponse,
    responses={
        200: {"description": "查询成功"},
        404: {"description": "API Key 不存在"},
    },
)
async def get_key(
    api_key_prefix: str,
    _checked: APIKeyResponse = Depends(_check_api_key_permission),
) -> ApiResponse[Any]:
    """根据前缀查询 API Key"""
    return ApiResponse(data=_checked)


# ==================== 更新接口 ====================


@router.put(
    "/{api_key_prefix}",
    summary="更新 API Key",
    description="根据 API Key 前缀更新元数据信息",
    response_model=ApiResponse,
    responses={
        200: {"description": "更新成功"},
        403: {"description": "无权限执行此操作"},
        404: {"description": "API Key 不存在"},
    },
)
@inject
async def update_key(
    api_key_prefix: str,
    data: UpdateAPIKeyRequest,
    _checked: APIKeyResponse = Depends(_check_api_key_permission),
    op_ctx: OperationContext = Depends(get_op_ctx),
    service: APIKeyService = Depends(
        Provide[ApplicationContainer.services.api_key_service]
    ),
) -> ApiResponse[Any]:
    """根据前缀更新 API Key（仅允许修改 key_name 和 description）"""
    update_data = APIKeyUpdate(
        key_name=data.key_name,
        description=data.description,
    )
    key = await service.update_key_by_prefix(api_key_prefix, update_data, op_ctx)
    logger.info(f"Audit: update_key operator={op_ctx.operator} prefix={api_key_prefix}")
    return ApiResponse(data=key)


# ==================== 状态操作接口 ====================


@router.patch(
    "/{api_key_prefix}/status",
    summary="更新 API Key 状态",
    description="启用、停用或吊销 API Key",
    response_model=ApiResponse,
    responses={
        200: {"description": "操作成功"},
        403: {"description": "无权限执行此操作"},
        404: {"description": "API Key 不存在"},
        400: {"description": "状态不允许此操作"},
    },
)
@inject
async def update_key_status(
    api_key_prefix: str,
    data: StatusActionRequest,
    _checked: APIKeyResponse = Depends(_check_api_key_permission),
    op_ctx: OperationContext = Depends(get_op_ctx),
    service: APIKeyService = Depends(
        Provide[ApplicationContainer.services.api_key_service]
    ),
) -> ApiResponse[Any]:
    """根据前缀更新 API Key 状态"""
    try:
        if data.action == "activate":
            key = await service.activate_by_prefix(api_key_prefix, op_ctx)
            message = "API Key 已启用"
        elif data.action == "deactivate":
            key = await service.deactivate_by_prefix(api_key_prefix, op_ctx)
            message = "API Key 已停用"
        else:  # revoke
            key = await service.revoke_by_prefix(api_key_prefix, op_ctx)
            message = "API Key 已吊销"

        if not key:
            raise HTTPException(status_code=404, detail="API Key 不存在")
        logger.info(
            f"Audit: update_key_status operator={op_ctx.operator} prefix={api_key_prefix} action={data.action}"
        )
        return ApiResponse(data=key, message=message)
    except APIKeyError as e:
        raise HTTPException(status_code=400, detail=e.message)


# ==================== allowed-bots 接口 ====================


async def _validate_allowed_bots_context(
    bot_id: str,
    _checked: APIKeyResponse,
    op_ctx: OperationContext,
) -> None:
    """校验 allowed-bots 操作的前置条件：app_type、bot_id 格式、owner 权限"""
    if _checked.app_type != "app":
        raise HTTPException(
            status_code=400, detail="仅 app_type=app 的 API Key 支持 allowed_bots 操作"
        )

    # 验证 bot_id 格式必须为 real_bot_id:entity_id
    entity_id = parse_bot_entity_id(bot_id)
    if entity_id is None:
        raise HTTPException(
            status_code=400,
            detail="bot_id 格式无效，必须为 real_bot_id:entity_id",
        )

    # 权限校验：仅 bot owner 可操作
    operator = op_ctx.operator
    if not check_bot_permission(operator, bot_id):
        raise HTTPException(status_code=403, detail="无权限执行此操作")


@router.get(
    "/{api_key_prefix}/allowed-bots",
    summary="查询 allowed_bots",
    description="获取 app_type=app 的 API Key 的 policy 中的 allowed_bots 列表",
    response_model=ApiResponse,
    responses={
        200: {"description": "查询成功"},
        400: {"description": "非 app 类型 Key"},
        403: {"description": "无权限执行此操作"},
        404: {"description": "API Key 不存在"},
    },
)
async def get_allowed_bots(
    api_key_prefix: str,
    _checked: APIKeyResponse = Depends(_check_api_key_permission),
) -> ApiResponse[Any]:
    """获取 API Key 的 allowed_bots 列表（仅 app_type=app）"""
    if _checked.app_type != "app":
        raise HTTPException(
            status_code=400, detail="仅 app_type=app 的 API Key 支持 allowed_bots 操作"
        )

    policy = parse_policy(_checked.policy)
    # 过滤 NONE 哨兵值，对外返回语义为空列表
    bots = [b for b in policy.allowed_bots if b != "NONE"]
    logger.info(f"Get allowed_bots: prefix={api_key_prefix}, count={len(bots)}")
    return ApiResponse(data={"allowed_bots": bots})


@router.post(
    "/{api_key_prefix}/allowed-bots/grant",
    summary="授权 bot 访问",
    description="向 app_type=app 的 API Key 授权一个 bot 访问权限。权限语义：bot owner（operator == entity_id）授权自己的 bot 给 app key，而非 app key owner 操作",
    response_model=ApiResponse,
    responses={
        200: {"description": "授权成功"},
        400: {"description": "请求参数错误或非 app 类型 Key"},
        403: {"description": "无权限执行此操作"},
        404: {"description": "API Key 不存在"},
    },
)
@inject
async def grant_allowed_bot(
    api_key_prefix: str,
    data: BotIdRequest,
    _key: APIKeyResponse = Depends(_get_api_key_or_404),
    op_ctx: OperationContext = Depends(get_op_ctx),
    service: APIKeyService = Depends(
        Provide[ApplicationContainer.services.api_key_service]
    ),
) -> ApiResponse[Any]:
    """授权 bot 访问（仅 app_type=app），仅 bot owner 可操作"""
    await _validate_allowed_bots_context(data.bot_id, _key, op_ctx)

    policy = parse_policy(_key.policy)

    # 如果当前是 NONE 哨兵，清空后替换为实际 bot_id
    if policy.allowed_bots == ["NONE"]:
        policy.allowed_bots = []
    if data.bot_id not in set(policy.allowed_bots):
        policy.allowed_bots.append(data.bot_id)

    update_data = APIKeyUpdate(policy=policy.to_json())
    key = await service.update_key_by_prefix(api_key_prefix, update_data, op_ctx)
    if not key:
        raise HTTPException(status_code=404, detail="API Key 不存在")
    logger.info(
        f"Audit: grant_allowed_bot operator={op_ctx.operator} prefix={api_key_prefix} bot_id={data.bot_id}"
    )
    return ApiResponse(data=key, message="allowed_bots 已授权")


@router.post(
    "/{api_key_prefix}/allowed-bots/revoke",
    summary="撤销 bot 访问",
    description="撤销 app_type=app 的 API Key 中一个 bot 的访问权限。权限语义：bot owner（operator == entity_id）撤销自己的 bot 对 app key 的访问，而非 app key owner 操作",
    response_model=ApiResponse,
    responses={
        200: {"description": "撤销成功"},
        400: {"description": "请求参数错误或非 app 类型 Key"},
        403: {"description": "无权限执行此操作"},
        404: {"description": "API Key 不存在"},
    },
)
@inject
async def revoke_allowed_bot(
    api_key_prefix: str,
    data: BotIdRequest,
    _key: APIKeyResponse = Depends(_get_api_key_or_404),
    op_ctx: OperationContext = Depends(get_op_ctx),
    service: APIKeyService = Depends(
        Provide[ApplicationContainer.services.api_key_service]
    ),
) -> ApiResponse[Any]:
    """撤销 bot 访问（仅 app_type=app），仅 bot owner 可操作"""
    await _validate_allowed_bots_context(data.bot_id, _key, op_ctx)

    policy = parse_policy(_key.policy)

    if data.bot_id not in set(policy.allowed_bots):
        raise HTTPException(status_code=404, detail="bot_id 不在 allowed_bots 中")
    policy.allowed_bots = [b for b in policy.allowed_bots if b != data.bot_id]

    update_data = APIKeyUpdate(policy=policy.to_json())
    key = await service.update_key_by_prefix(api_key_prefix, update_data, op_ctx)
    if not key:
        raise HTTPException(status_code=404, detail="API Key 不存在")
    logger.info(
        f"Audit: revoke_allowed_bot operator={op_ctx.operator} prefix={api_key_prefix} bot_id={data.bot_id}"
    )
    return ApiResponse(data=key, message="allowed_bots 已撤销")
