"""Device Template REST API routes.

Exposes DeviceTemplateService methods as HTTP endpoints.

Endpoints:
- GET /api/v1/device-templates - List templates with tenant isolation
- GET /api/v1/device-templates/online - List ONLINE templates
- GET /api/v1/device-templates/by-template-id/{template_id} - Get template by template_id
- GET /api/v1/device-templates/resolve - Get default or explicit template
- GET /api/v1/device-templates/{template_uuid} - Get template by UUID
- POST /api/v1/device-templates - Create template
- POST /api/v1/device-templates/{template_uuid}/status-transitions - Update template status
- PUT /api/v1/device-templates/{template_uuid} - Update template
- POST /api/v1/device-templates/{template_uuid}/delete - Soft delete template
"""

from typing import Annotated

from dependency_injector.wiring import inject
from fastapi import APIRouter, Depends, HTTPException, Path, Query
from pydantic import BaseModel, Field

from secbaas.api import ApiResponse, SuccessResponse
from secbaas.api.template_manage import (
    DeviceTemplateManageService,
    DeviceTemplateResponse,
    TemplateCreate,
    TemplateListResponse,
    TemplateStatus,
    TemplateUpdate,
)
from secbaas.bootstrap import ApplicationContainer, Provide
from secbaas.logger import get_logger


class StatusTransitionRequest(BaseModel):
    """Request model for template status transition."""

    current_status: TemplateStatus = Field(..., description="当前状态（用于定位记录）")
    new_status: TemplateStatus = Field(..., description="新状态")


logger = get_logger("router")

router = APIRouter(prefix="/api/v1/device-templates", tags=["设备模板管理"])


@router.get("", response_model=ApiResponse[TemplateListResponse])
@inject
async def list_templates(
    tenant: Annotated[str, Query(description="租户名称")],
    status: Annotated[TemplateStatus | None, Query(description="模板状态过滤")] = None,
    page: Annotated[int, Query(ge=1, description="页码")] = 1,
    page_size: Annotated[int, Query(ge=1, le=100, description="每页数量")] = 20,
    service: DeviceTemplateManageService = Depends(
        Provide[ApplicationContainer.services.device_template_service]
    ),
) -> ApiResponse[TemplateListResponse]:
    """List device templates for a tenant with optional status filter."""

    logger.info(
        f"Listing templates: tenant={tenant}, status={status}, page={page}, page_size={page_size}"
    )
    result = service.list_templates(
        tenant=tenant,
        status=status,
        page=page,
        page_size=page_size,
    )
    return ApiResponse(data=result)


@router.get("/online", response_model=ApiResponse[TemplateListResponse])
@inject
async def list_online_templates(
    tenant: Annotated[str, Query(description="租户名称")],
    page: Annotated[int, Query(ge=1, description="页码")] = 1,
    page_size: Annotated[int, Query(ge=1, le=100, description="每页数量")] = 20,
    service: DeviceTemplateManageService = Depends(
        Provide[ApplicationContainer.services.device_template_service]
    ),
) -> ApiResponse[TemplateListResponse]:
    """List ONLINE status templates for bot creation."""

    logger.info(
        f"Listing ONLINE templates: tenant={tenant}, page={page}, page_size={page_size}"
    )
    result = service.list_online_templates(
        tenant=tenant,
        page=page,
        page_size=page_size,
    )
    return ApiResponse(data=result)


@router.get(
    "/by-template-id/{template_id}", response_model=ApiResponse[DeviceTemplateResponse]
)
@inject
async def get_template_by_id(
    template_id: Annotated[int, Path(ge=0, description="模板ID (PaaS平台租户业务ID)")],
    service: DeviceTemplateManageService = Depends(
        Provide[ApplicationContainer.services.device_template_service]
    ),
) -> ApiResponse[DeviceTemplateResponse]:
    """Get template by template_id (global unique, no tenant required).

    Note: template_id is globally unique across all tenants.
    """
    logger.info(f"Getting template by template_id: {template_id}")

    template = service.get_by_template_id(template_id)

    if not template:
        raise HTTPException(
            status_code=404,
            detail={
                "error_code": "TEMPLATE_NOT_FOUND",
                "message": f"Template not found by template_id: {template_id}",
            },
        )
    return ApiResponse(data=template)


@router.get("/resolve", response_model=ApiResponse[DeviceTemplateResponse])
@inject
async def resolve_template(
    tenant: Annotated[str, Query(description="租户名称")],
    template_uuid: Annotated[
        str | None, Query(description="模板UUID (可选，不提供则返回默认模板)")
    ] = None,
    service: DeviceTemplateManageService = Depends(
        Provide[ApplicationContainer.services.device_template_service]
    ),
) -> ApiResponse[DeviceTemplateResponse]:
    """Resolve default or explicit template for tenant.

    Two-tier lookup:
    1. If template_uuid provided: return that specific template
    2. Otherwise: return tenant's default_template_uuid from extra_config
    """
    logger.info(
        f"Resolving template: tenant={tenant}, template_uuid={template_uuid or 'default'}"
    )

    try:
        template = service.get_default_or_explicit_template(
            tenant=tenant,
            template_uuid=template_uuid,
        )
        return ApiResponse(data=template)
    except ValueError as e:
        raise HTTPException(
            status_code=404,
            detail={
                "error_code": "TEMPLATE_NOT_FOUND",
                "message": str(e),
            },
        )


@router.get("/{template_uuid}", response_model=ApiResponse[DeviceTemplateResponse])
@inject
async def get_template(
    template_uuid: Annotated[str, Path(description="模板UUID")],
    tenant: Annotated[str, Query(description="租户名称")],
    service: DeviceTemplateManageService = Depends(
        Provide[ApplicationContainer.services.device_template_service]
    ),
) -> ApiResponse[DeviceTemplateResponse]:
    """Get template by UUID with tenant isolation."""

    logger.info(f"Getting template: template_uuid={template_uuid}, tenant={tenant}")

    template = service.get_online_template_by_uuid(tenant, template_uuid)

    if not template:
        raise HTTPException(
            status_code=404,
            detail={
                "error_code": "TEMPLATE_NOT_FOUND",
                "message": f"Template not found: {tenant}/{template_uuid}",
            },
        )
    return ApiResponse(data=template)


@router.post("", status_code=201, response_model=ApiResponse[DeviceTemplateResponse])
@inject
async def create_template(
    request: TemplateCreate,
    tenant: Annotated[str, Query(description="租户名称")],
    service: DeviceTemplateManageService = Depends(
        Provide[ApplicationContainer.services.device_template_service]
    ),
) -> ApiResponse[DeviceTemplateResponse]:
    """Create new device template.

    Required fields:
    - template_uuid: Multi-version tracking UUID
    - template_id: PaaS platform tenant business ID (int)
    - type: Platform type (ARCA or Sigma)
    - name: Template name
    - operator: Operator ID
    """
    logger.info(
        f"Creating template: tenant={tenant}, "
        f"template_uuid={'auto' if request.template_uuid is None else 'provided'}, "
        f"template_id={request.template_id}, type={request.type}, name={request.name}"
    )

    result = service.create_template(tenant=tenant, data=request)
    return ApiResponse(data=result)


@router.put("/{template_uuid}", response_model=ApiResponse[DeviceTemplateResponse])
@inject
async def update_template(
    template_uuid: Annotated[str, Path(description="模板UUID")],
    request: TemplateUpdate,
    tenant: Annotated[str, Query(description="租户名称")],
    status: Annotated[
        TemplateStatus, Query(description="模板状态")
    ] = TemplateStatus.ONLINE,
    service: DeviceTemplateManageService = Depends(
        Provide[ApplicationContainer.services.device_template_service]
    ),
) -> ApiResponse[DeviceTemplateResponse]:
    """Update template.

    Note: update does not change template_uuid.
    Use this endpoint to update name, description, or config of an existing template.
    """
    logger.info(
        f"Updating template: template_uuid={template_uuid}, tenant={tenant}, "
        f"status={status}, operator={request.operator}"
    )

    # Update using composite key (tenant, template_uuid, status)
    updated = service.update_template(
        tenant=tenant,
        template_uuid=template_uuid,
        status=status,
        data=request,
    )

    if not updated:
        raise HTTPException(
            status_code=404,
            detail={
                "error_code": "TEMPLATE_NOT_FOUND",
                "message": f"Template not found: {tenant}/{template_uuid} with status {status}",
            },
        )
    return ApiResponse(data=updated)


@router.post(
    "/{template_uuid}/status-transitions",
    response_model=ApiResponse[DeviceTemplateResponse],
)
@inject
async def transition_template_status(
    template_uuid: Annotated[str, Path(description="模板UUID")],
    request: StatusTransitionRequest,
    tenant: Annotated[str, Query(description="租户名称")],
    service: DeviceTemplateManageService = Depends(
        Provide[ApplicationContainer.services.device_template_service]
    ),
) -> ApiResponse[DeviceTemplateResponse]:
    """Transition template status (e.g., CREATED -> AUDITED -> ONLINE <-> OFFLINE).

    Uses composite key (tenant, template_uuid, current_status) to locate record.
    """
    logger.info(
        f"Transitioning template status: template_uuid={template_uuid}, tenant={tenant}, "
        f"from={request.current_status.value} to={request.new_status.value}"
    )

    updated = service.update_status(
        tenant=tenant,
        template_uuid=template_uuid,
        current_status=request.current_status,
        new_status=request.new_status,
    )

    if not updated:
        raise HTTPException(
            status_code=404,
            detail={
                "error_code": "TEMPLATE_NOT_FOUND",
                "message": (
                    f"Template not found: {tenant}/{template_uuid} "
                    f"with status {request.current_status.value}"
                ),
            },
        )
    return ApiResponse(data=updated)


class DeleteTemplateRequest(BaseModel):
    """Request model for template deletion."""

    operator: str = Field(..., description="操作人 ID")


@router.post("/{template_uuid}/delete", response_model=ApiResponse[SuccessResponse])
@inject
async def delete_template(
    template_uuid: Annotated[str, Path(description="模板UUID")],
    request: DeleteTemplateRequest,
    tenant: Annotated[str, Query(description="租户名称")],
    status: Annotated[TemplateStatus, Query(description="模板状态 (用于定位记录)")],
    service: DeviceTemplateManageService = Depends(
        Provide[ApplicationContainer.services.device_template_service]
    ),
) -> ApiResponse[SuccessResponse]:
    """Soft delete template.

    Deletes template by composite key (tenant, template_uuid, status).
    Must specify exact status to locate the record for deletion.
    """
    logger.info(
        f"Deleting template: template_uuid={template_uuid}, tenant={tenant}, "
        f"status={status}, operator={request.operator}"
    )

    success = service.soft_delete_template(
        tenant=tenant,
        template_uuid=template_uuid,
        status=status,
        operator=request.operator,
    )

    if not success:
        raise HTTPException(
            status_code=404,
            detail={
                "error_code": "TEMPLATE_NOT_FOUND",
                "message": f"Template not found: {tenant}/{template_uuid} with status {status}",
            },
        )

    return ApiResponse(data=SuccessResponse(message="Template deleted"))
