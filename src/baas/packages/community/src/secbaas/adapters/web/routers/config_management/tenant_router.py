"""Tenant REST API routes.

Exposes TenantService methods as HTTP endpoints.

Endpoints:
- GET /api/v1/tenants - List tenants
- GET /api/v1/tenants/{name} - Get tenant by name
- GET /api/v1/tenants/{name}/config - Get tenant configuration
- POST /api/v1/tenants - Create tenant
- PUT /api/v1/tenants/{name} - Update tenant
- DELETE /api/v1/tenants/{name} - Soft delete tenant
"""

from typing import Annotated

from dependency_injector.wiring import inject
from fastapi import APIRouter, Depends, HTTPException, Path, Query

from secbaas.api import ApiResponse, SuccessResponse
from secbaas.api.tenant_manage import (
    TenantConfig,
    TenantCreate,
    TenantListResponse,
    TenantManageService,
    TenantResponse,
    TenantUpdate,
)
from secbaas.bootstrap import ApplicationContainer, Provide
from secbaas.logger import get_logger

logger = get_logger("router")

router = APIRouter(prefix="/api/v1/tenants", tags=["租户管理"])


@router.get("", response_model=ApiResponse[TenantListResponse])
@inject
async def list_tenants(
    page: Annotated[int, Query(ge=1, description="页码")] = 1,
    page_size: Annotated[int, Query(ge=1, le=100, description="每页数量")] = 20,
    service: TenantManageService = Depends(
        Provide[ApplicationContainer.services.tenant_service]
    ),
) -> ApiResponse[TenantListResponse]:
    """List tenants with optional env filter."""

    logger.info(f"Listing tenants: page={page}, page_size={page_size}")
    result = service.list_tenants(page=page, page_size=page_size)
    return ApiResponse(data=result)


@router.get("/{name}", response_model=ApiResponse[TenantResponse])
@inject
async def get_tenant(
    name: Annotated[str, Path(description="租户名称")],
    service: TenantManageService = Depends(
        Provide[ApplicationContainer.services.tenant_service]
    ),
) -> ApiResponse[TenantResponse]:
    """Get tenant by name."""

    logger.info(f"Getting tenant: name={name}")

    # Only use name lookup now (no ID lookup)
    tenant = service.get_tenant_by_name(name)

    if not tenant:
        raise HTTPException(
            status_code=404,
            detail={
                "error_code": "TENANT_NOT_FOUND",
                "message": f"Tenant not found: {name}",
            },
        )
    return ApiResponse(data=tenant)


@router.get("/{name}/config", response_model=ApiResponse[TenantConfig])
@inject
async def get_tenant_config(
    name: Annotated[str, Path(description="租户名称")],
    service: TenantManageService = Depends(
        Provide[ApplicationContainer.services.tenant_service]
    ),
) -> ApiResponse[TenantConfig]:
    """Get tenant configuration (extra_config)."""

    logger.info(f"Getting tenant config: name={name}")

    config = service.get_tenant_config(name)

    if not config:
        raise HTTPException(
            status_code=404,
            detail={
                "error_code": "TENANT_NOT_FOUND",
                "message": f"Tenant not found: {name}",
            },
        )
    return ApiResponse(data=config)


@router.post("", status_code=201, response_model=ApiResponse[TenantResponse])
@inject
async def create_tenant(
    request: TenantCreate,
    service: TenantManageService = Depends(
        Provide[ApplicationContainer.services.tenant_service]
    ),
) -> ApiResponse[TenantResponse]:
    logger.info(f"Creating tenant: name={request.name}, operator={request.operator}")
    result = service.create_tenant(data=request)
    return ApiResponse(data=result)


@router.put("/{name}", response_model=ApiResponse[TenantResponse])
@inject
async def update_tenant(
    name: Annotated[str, Path(description="租户名称")],
    request: TenantUpdate,
    service: TenantManageService = Depends(
        Provide[ApplicationContainer.services.tenant_service]
    ),
) -> ApiResponse[TenantResponse]:
    """Update tenant (description, extra_config only).

    env and modifier are optional. When not provided:
    - env is derived from get_current_env()
    - modifier uses the value from request (no fallback)
    """

    logger.info(f"Updating tenant: name={name}")

    tenant = service.update_tenant(
        name=name,
        data=request,
    )

    if not tenant:
        raise HTTPException(
            status_code=404,
            detail={
                "error_code": "TENANT_NOT_FOUND",
                "message": f"Tenant not found: {name}",
            },
        )
    return ApiResponse(data=tenant)


@router.delete("/{name}", response_model=ApiResponse[SuccessResponse])
@inject
async def delete_tenant(
    name: Annotated[str, Path(description="租户名称")],
    operator: Annotated[str, Query(description="操作人")] = "unknown",
    service: TenantManageService = Depends(
        Provide[ApplicationContainer.services.tenant_service]
    ),
) -> ApiResponse[SuccessResponse]:
    """Soft delete tenant.

    env is optional. When not provided, it is derived from get_current_env().
    operator is optional and defaults to "unknown".
    """

    logger.info(f"Deleting tenant: name={name}, operator={operator}")

    success = service.soft_delete_tenant(
        name=name,
        operator=operator,
    )

    if not success:
        raise HTTPException(
            status_code=404,
            detail={
                "error_code": "TENANT_NOT_FOUND",
                "message": f"Tenant not found: {name}",
            },
        )

    return ApiResponse(data=SuccessResponse(message="Tenant deleted"))
