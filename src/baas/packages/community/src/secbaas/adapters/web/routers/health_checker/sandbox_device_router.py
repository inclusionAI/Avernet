"""Sandbox Device Router - 沙箱设备管理 HTTP 端点。

提供按 table_type 路由的沙箱设备查询、告警、续期接口。
复用 Bot Health Checker 的 API Key 校验逻辑。

Endpoints:
- GET /api/v1/sandbox-device/active-sandboxes - 分页查询激活沙箱设备
- POST /api/v1/sandbox-device/probe-and-warn - 探活告警（探活/阈值判断）
- POST /api/v1/sandbox-device/renew-ttl - 续期
"""

from typing import Annotated

from dependency_injector.wiring import inject
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from secbaas.adapters.web.routers.open_api.dependencies import validate_api_key
from secbaas.api import ApiResponse
from secbaas.api.api_gateway import APIKeyRecord
from secbaas.api.health_check.sandbox import (
    PaginatedResult,
    RenewTtlResult,
    SandboxDeviceRouter,
    WarnResult,
)
from secbaas.bootstrap import ApplicationContainer, Provide
from secbaas.logger import get_logger

logger = get_logger("router")

router = APIRouter(prefix="/api/v1/sandbox-device", tags=["沙箱设备管理"])

# 允许访问沙箱设备管理接口的 app_type
SANDBOX_DEVICE_ALLOWED_APP_TYPES = {"health-checker"}


async def validate_sandbox_device_api_key(
    api_key_record: APIKeyRecord = Depends(validate_api_key),
) -> APIKeyRecord:
    """验证 API Key 并检查 app_type 是否允许访问沙箱设备管理接口。"""
    if api_key_record.app_type not in SANDBOX_DEVICE_ALLOWED_APP_TYPES:
        logger.warning(
            f"[validate_sandbox_device_api_key] API Key app_type not allowed: "
            f"app_id={api_key_record.app_id}, app_type={api_key_record.app_type}"
        )
        allowed = ", ".join(SANDBOX_DEVICE_ALLOWED_APP_TYPES)
        raise HTTPException(
            status_code=403,
            detail={
                "error_code": "FORBIDDEN",
                "message": f"API Key app_type must be one of: {allowed}",
            },
        )
    return api_key_record


# ---------------------------------------------------------------------------
# Request / Response Models
# ---------------------------------------------------------------------------


class WarnRequest(BaseModel):
    table_id: int = Field(..., description="表主键 ID")
    table_type: str = Field(..., description="表类型: ac_binding | baas")


class RenewTtlRequest(BaseModel):
    table_id: int = Field(..., description="表主键 ID")
    table_type: str = Field(..., description="表类型: ac_binding | baas")


class SandboxDeviceInfoResponse(BaseModel):
    table_id: int
    table_type: str
    sandbox_id: str | None
    ttl_expiration_time: str | None
    ttl_expiration_timestamp: int | None
    refresh_fail_count: int
    status: str


class PaginatedResponse(BaseModel):
    total: int
    page: int
    page_size: int
    items: list[SandboxDeviceInfoResponse]


class WarnResponse(BaseModel):
    table_id: int
    table_type: str
    action: str
    refresh_fail_count: int


class RenewTtlResponse(BaseModel):
    table_id: int
    table_type: str
    device_id: str
    success: bool
    old_expiration_time: str | None
    new_expiration_time: str | None
    refresh_fail_count: int
    error: str | None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/active-sandboxes", response_model=ApiResponse[PaginatedResponse])
@inject
async def list_active_sandboxes(
    api_key_record: APIKeyRecord = Depends(validate_sandbox_device_api_key),
    env: Annotated[str, Query(description="环境: dev/pre/prod")] = "prod",
    table_type: Annotated[str, Query(description="表类型: ac_binding | baas")] = ...,
    page: Annotated[int, Query(ge=1, description="页码")] = 1,
    page_size: Annotated[int, Query(ge=1, le=100, description="每页条数")] = 20,
    device_router: SandboxDeviceRouter = Depends(
        Provide[ApplicationContainer.services.sandbox_device_router]
    ),
):
    """分页查询激活状态的沙箱设备。"""
    r = device_router
    try:
        result: PaginatedResult = r.query_active_sandboxes(
            env=env, table_type=table_type, page=page, page_size=page_size
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"[sandbox-device:active-sandboxes] Failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

    return ApiResponse(
        code=0,
        message="success",
        data=PaginatedResponse(
            total=result.total,
            page=result.page,
            page_size=result.page_size,
            items=[
                SandboxDeviceInfoResponse(
                    table_id=item.table_id,
                    table_type=item.table_type,
                    sandbox_id=item.sandbox_id,
                    ttl_expiration_time=item.ttl_expiration_time,
                    ttl_expiration_timestamp=item.ttl_expiration_timestamp,
                    refresh_fail_count=item.refresh_fail_count,
                    status=item.status,
                )
                for item in result.items
            ],
        ),
    )


@router.post("/probe-and-warn", response_model=ApiResponse[WarnResponse])
@inject
async def probe_and_warn(
    request: WarnRequest,
    api_key_record: APIKeyRecord = Depends(validate_sandbox_device_api_key),
    device_router: SandboxDeviceRouter = Depends(
        Provide[ApplicationContainer.services.sandbox_device_router]
    ),
):
    """探活告警：探活成功清零，失败 +1，超阈值置 WARNING。"""
    r = device_router
    try:
        result: WarnResult = await r.warn_device(
            table_id=request.table_id, table_type=request.table_type
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"[sandbox-device:probe-and-warn] Failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

    return ApiResponse(
        code=0,
        message="success",
        data=WarnResponse(
            table_id=result.table_id,
            table_type=result.table_type,
            action=result.action,
            refresh_fail_count=result.refresh_fail_count,
        ),
    )


@router.post("/renew-ttl", response_model=ApiResponse[RenewTtlResponse])
@inject
async def renew_ttl(
    request: RenewTtlRequest,
    api_key_record: APIKeyRecord = Depends(validate_sandbox_device_api_key),
    device_router: SandboxDeviceRouter = Depends(
        Provide[ApplicationContainer.services.sandbox_device_router]
    ),
):
    """续期：调用 Arca 续期，成功更新 TTL 并清零，失败 +1。"""
    r = device_router
    try:
        result: RenewTtlResult = await r.renew_ttl(
            table_type=request.table_type,
            table_id=request.table_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"[sandbox-device:renew-ttl] Failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

    return ApiResponse(
        code=0,
        message="success",
        data=RenewTtlResponse(
            table_id=result.table_id,
            table_type=result.table_type,
            device_id=result.device_id,
            success=result.success,
            old_expiration_time=result.old_expiration_time,
            new_expiration_time=result.new_expiration_time,
            refresh_fail_count=result.refresh_fail_count,
            error=result.error,
        ),
    )
