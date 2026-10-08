"""Local PaaS Service REST API routes.

Exposes LocalPaasService machine management APIs as HTTP endpoints.
Device lifecycle operations are now in paas_facade_router.

Endpoints:
- GET /api/v1/local/machines/{machine_id}/info - Get machine info via LocalPaasService
- GET /api/v1/local/users/{user_id}/machines - List machines by user via LocalPaasService
"""

from datetime import datetime
from typing import Annotated, Any

from dependency_injector.wiring import inject
from fastapi import APIRouter, Depends, HTTPException, Path, Query
from pydantic import BaseModel, Field

from secbaas.community.api import ApiResponse
from secbaas.community.api.device_manage import DeviceCreationError, LocalPaasService
from secbaas.community.bootstrap import ApplicationContainer, Provide
from secbaas.community.logger import get_logger

logger = get_logger("router")

router = APIRouter(prefix="/api/v1/local", tags=["本地设备管理"])


# ============================================================================
# Pydantic Models
# ============================================================================


class UserMachineResponse(BaseModel):
    """User machine record response model.

    Returned by the user machines list endpoint. Contains machine
    details from baas_local_user_machine table (internal fields excluded).
    """

    template_id: int = Field(..., description="模板ID")
    user_id: str = Field(..., description="用户ID")
    machine_id: str = Field(..., description="机器ID")
    machine_info: dict[str, Any] = Field(..., description="机器信息（JSON对象）")
    last_heartbeat: datetime = Field(..., description="最后心跳时间")
    connected_server_instance: str = Field(..., description="连接的服务器实例")
    status: str = Field(..., description="机器状态 (ONLINE/OFFLINE/DISABLED)")
    env: str = Field(..., description="环境标识")


# ============================================================================
# Endpoints
# ============================================================================


@router.get(
    "/machines/{machine_id}/info",
    response_model=ApiResponse[dict[str, Any]],
)
@inject
async def get_machine_info(
    machine_id: Annotated[
        str,
        Path(
            min_length=1,
            description="机器ID",
        ),
    ],
    local_paas_service: LocalPaasService = Depends(
        Provide[ApplicationContainer.services.system_default_local_paas_service]
    ),
) -> ApiResponse[dict[str, Any]]:
    """Get machine resource information from mng daemon.

    Args:
        machine_id: The machine identifier to query

    Returns:
        Dictionary with machine resource info (cpu_cores, memory_gb, disk_gb, etc.)

    Raises:
        HTTPException: On retrieval failure with appropriate status code:
            - 404: MACHINE_NOT_FOUND
            - 500: QUERY_FAILED
    """
    logger.info("Getting machine info: %s", machine_id)

    try:
        result = await local_paas_service.get_machine_info(machine_id=machine_id)
        logger.info("Machine info retrieved successfully: %s", machine_id)
        return ApiResponse(data=result)

    except DeviceCreationError:
        raise
    except Exception as e:
        logger.error("Unexpected error getting machine info: %s", e, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={
                "error_code": "INTERNAL_ERROR",
                "message": "An internal error occurred",
                "context": {
                    "operation": "get_machine_info",
                    "machine_id": machine_id,
                },
            },
        )


@router.get(
    "/machines/{machine_id}/res-dirs",
    response_model=ApiResponse[dict[str, Any]],
)
@inject
async def get_machine_res_dirs(
    machine_id: Annotated[
        str,
        Path(
            min_length=1,
            description="机器ID",
        ),
    ],
    dir: Annotated[
        str,
        Query(
            description="查询目录路径，默认为~/Desktop",
        ),
    ] = "~/Desktop",
    local_paas_service: LocalPaasService = Depends(
        Provide[ApplicationContainer.services.system_default_local_paas_service]
    ),
) -> ApiResponse[dict[str, Any]]:
    """Get machine resource directory structure from mng daemon.

    Queries mng daemon for directory tree structure on the specified machine.
    Path validation prevents directory traversal attacks.

    Args:
        machine_id: The machine identifier to query
        dir: The directory path to query (default: ~/Desktop)

    Returns:
        Directory tree structure: {name: string, children?: [...]}
        Files have only name, directories have both name and children.

    Raises:
        HTTPException: On query failure with appropriate status code:
            - 400: INVALID_PARAMS (path contains .. or starts with /)
            - 403: PERMISSION_DENIED (read access denied)
            - 404: MACHINE_NOT_FOUND or PATH_NOT_FOUND
            - 500: QUERY_FAILED or INVALID_RESPONSE
            - 503: MACHINE_OFFLINE (with diagnostic context)
    """
    logger.info("Getting machine resource directories: %s, dir=%s", machine_id, dir)

    try:
        result = await local_paas_service.get_machine_res_dirs(
            machine_id=machine_id, dir=dir
        )
        logger.info(
            "Machine resource directories retrieved successfully: %s", machine_id
        )
        return ApiResponse(data=result)

    except DeviceCreationError:
        raise
    except Exception as e:
        logger.error(
            "Unexpected error getting machine resource directories: %s",
            e,
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail={
                "error_code": "INTERNAL_ERROR",
                "message": "An internal error occurred",
                "context": {
                    "operation": "get_machine_res_dirs",
                    "machine_id": machine_id,
                    "dir": dir,
                },
            },
        )


@router.get(
    "/users/{user_id}/machines",
    response_model=ApiResponse[list[UserMachineResponse]],
)
@inject
async def list_user_machines(
    user_id: Annotated[
        str,
        Path(
            min_length=1,
            description="用户ID",
        ),
    ],
    local_paas_service: LocalPaasService = Depends(
        Provide[ApplicationContainer.services.system_default_local_paas_service]
    ),
) -> ApiResponse[list[UserMachineResponse]]:
    """Get all machines for a specific user.

    Retrieves the list of all machines associated with the given user_id
    from the baas_local_user_machine table for the current environment.

    Args:
        user_id: The user identifier to query machines for

    Returns:
        List of UserMachineResponse containing machine details.
        Returns empty list (not error) if user has no machines.

    Raises:
        HTTPException: On query failure with appropriate status code:
            - 500: INTERNAL_ERROR for unexpected errors
    """
    logger.info("Listing machines for user: %s", user_id)

    try:
        # Call service method to get machine records
        records = await local_paas_service.list_machines_by_user(user_id=user_id)

        # Map records to response models (exclude internal fields: id, gmt_create, gmt_modified)
        machines = [
            UserMachineResponse(
                template_id=record.template_id,
                user_id=record.user_id,
                machine_id=record.machine_id,
                machine_info=record.machine_info,
                last_heartbeat=record.last_heartbeat,
                connected_server_instance=record.connected_server_instance,
                status=record.status,
                env=record.env,
            )
            for record in records
        ]

        logger.info("Found %d machines for user: %s", len(machines), user_id)
        return ApiResponse(data=machines)

    except DeviceCreationError:
        raise
    except Exception as e:
        logger.error("Unexpected error listing user machines: %s", e, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={
                "error_code": "INTERNAL_ERROR",
                "message": "An internal error occurred",
                "context": {
                    "operation": "list_user_machines",
                    "user_id": user_id,
                },
            },
        )
