"""System config REST API routes.

Exposes SystemConfigManageService methods as HTTP endpoints.

Endpoints:
- GET /api/v1/system-configs - List configs
- GET /api/v1/system-configs/{conf_key}?env=xxx - Get config
- POST /api/v1/system-configs - Create config
- PUT /api/v1/system-configs/{conf_key}?env=xxx - Update config
- DELETE /api/v1/system-configs/{conf_key}?env=xxx - Delete config
"""

from typing import Annotated

from dependency_injector.wiring import inject
from fastapi import APIRouter, Depends, HTTPException, Path, Query

from secbaas.community.adapters.web.dependencies import get_op_ctx
from secbaas.community.api import ApiResponse, OperationContext, SuccessResponse
from secbaas.community.api.config_manage import (
    SystemConfigCreate,
    SystemConfigListResponse,
    SystemConfigManageService,
    SystemConfigResponse,
    SystemConfigUpdate,
)
from secbaas.community.bootstrap import ApplicationContainer, Provide
from secbaas.community.logger import get_logger

logger = get_logger("router")

router = APIRouter(prefix="/api/v1/system-configs", tags=["系统配置管理"])


@router.get("", response_model=ApiResponse[SystemConfigListResponse])
@inject
async def list_configs(
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    op_ctx: OperationContext = Depends(get_op_ctx),
    service: SystemConfigManageService = Depends(
        Provide[ApplicationContainer.services.system_config_service]
    ),
) -> ApiResponse[SystemConfigListResponse]:
    """List system configs with optional env filter."""

    logger.info(
        "Listing system configs: page=%s, page_size=%s, operator=%s",
        page,
        page_size,
        op_ctx.operator,
    )
    result = service.list_configs(page=page, page_size=page_size)
    return ApiResponse(data=result)


@router.get("/{conf_key}", response_model=ApiResponse[SystemConfigResponse])
@inject
async def get_config(
    conf_key: Annotated[str, Path(description="配置键")],
    op_ctx: OperationContext = Depends(get_op_ctx),
    service: SystemConfigManageService = Depends(
        Provide[ApplicationContainer.services.system_config_service]
    ),
) -> ApiResponse[SystemConfigResponse]:
    """Get system config by key with optional env."""

    logger.info(
        "Getting system config: conf_key=%s, operator=%s", conf_key, op_ctx.operator
    )

    config = service.get_config(conf_key=conf_key)
    if not config:
        raise HTTPException(
            status_code=404,
            detail={
                "error_code": "CONFIG_NOT_FOUND",
                "message": f"Config not found: {conf_key}",
            },
        )
    return ApiResponse(data=config)


@router.post("", status_code=201, response_model=ApiResponse[SystemConfigResponse])
@inject
async def create_config(
    request: SystemConfigCreate,
    service: SystemConfigManageService = Depends(
        Provide[ApplicationContainer.services.system_config_service]
    ),
    op_ctx: OperationContext = Depends(get_op_ctx),
) -> ApiResponse[SystemConfigResponse]:
    """Create a new system config.

    env and operator are optional. When not provided:
    - env is derived from get_current_env()
    - creator uses the value from request (no fallback)
    """
    request.operator = op_ctx.operator
    logger.info(
        "Creating system config: conf_key=%s, creator=%s",
        request.conf_key,
        request.operator,
    )
    result = service.create_config(data=request)
    return ApiResponse(data=result)


@router.put("/{conf_key}", response_model=ApiResponse[SystemConfigResponse])
@inject
async def update_config(
    conf_key: Annotated[str, Path(description="配置键")],
    request: SystemConfigUpdate,
    service: SystemConfigManageService = Depends(
        Provide[ApplicationContainer.services.system_config_service]
    ),
    op_ctx: OperationContext = Depends(get_op_ctx),
) -> ApiResponse[SystemConfigResponse]:
    """Update system config.

    env and modifier are optional. When not provided:
    - env is derived from get_current_env()
    - modifier uses the value from request (no fallback)
    """

    logger.info("Updating system config: conf_key=%s", conf_key)
    request.operator = op_ctx.operator

    config = service.update_config(conf_key=conf_key, data=request)
    if not config:
        raise HTTPException(
            status_code=404,
            detail={
                "error_code": "CONFIG_NOT_FOUND",
                "message": f"Config not found: {conf_key}",
            },
        )
    return ApiResponse(data=config)


@router.delete("/{conf_key}", response_model=ApiResponse[SuccessResponse])
@inject
async def delete_config(
    conf_key: Annotated[str, Path(description="配置键")],
    service: SystemConfigManageService = Depends(
        Provide[ApplicationContainer.services.system_config_service]
    ),
    op_ctx: OperationContext = Depends(get_op_ctx),
) -> ApiResponse[SuccessResponse]:
    """Delete system config.

    env is optional. When not provided, it is derived from get_current_env().
    operator is optional and should be provided by caller.
    """

    operator = op_ctx.operator
    logger.info(
        "Deleting system config: conf_key=%s, operator=%s",
        conf_key,
        operator,
    )

    success = service.delete_config(conf_key=conf_key)
    if not success:
        raise HTTPException(
            status_code=404,
            detail={
                "error_code": "CONFIG_NOT_FOUND",
                "message": f"Config not found: {conf_key}",
            },
        )
    return ApiResponse(data=SuccessResponse(message="Config deleted"))
