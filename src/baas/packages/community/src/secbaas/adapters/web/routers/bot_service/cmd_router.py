"""Bot Command Execution REST API routes.

Provides an endpoint to execute shell commands on a bot's active device,
by selecting an available device and delegating to PaasServiceFacade.
"""

from typing import Annotated

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from pydantic import BaseModel, Field

from secbaas.api import ApiResponse
from secbaas.api.bot_runtime import (
    BotCmdDispatcher,
    BotNotFoundError,
    NoActiveDevicesError,
    NoDevicesFoundError,
)
from secbaas.api.device_manage import CommandResult, DeviceFacadeException
from secbaas.bootstrap import ApplicationContainer
from secbaas.logger import get_logger

logger = get_logger("router")

router = APIRouter(prefix="/api/v1/bots", tags=["Bot命令执行"])


class ExecuteCommandRequest(BaseModel):
    """Request model for executing a shell command on a bot's device."""

    cmd: str = Field(
        ...,
        min_length=1,
        max_length=8192,
        description="Shell command to execute on the device",
    )
    env: dict[str, str] | None = Field(
        default=None,
        description="Optional environment variables for the command",
    )
    timeout_seconds: int = Field(
        default=30,
        ge=1,
        le=300,
        description="Command timeout in seconds (1-300, default 30)",
    )


@router.post(
    "/{tenant}/{bot_uuid}/execute-command",
    response_model=ApiResponse[CommandResult],
    summary="Execute a shell command on a bot's active device",
    description="Execute a shell command on a bot's active device. The bot is resolved by UUID, an active device is selected, and the command is executed via the PaaS layer.",
)
@inject
async def execute_command_on_bot(
    tenant: Annotated[str, Path(description="Tenant for isolation")],
    bot_uuid: Annotated[str, Path(description="Bot UUID (business identifier)")],
    request: ExecuteCommandRequest,
    device_affinity: Annotated[
        str | None,
        Query(
            description="Device affinity key for consistent hashing-based sticky device selection"
        ),
    ] = None,
    dispatcher: BotCmdDispatcher = Depends(
        Provide[ApplicationContainer.services.bot_cmd_dispatcher]
    ),
) -> ApiResponse[CommandResult]:
    """Execute a shell command on a bot's active device.

    Args:
        tenant: Tenant for multi-tenancy isolation
        bot_uuid: Bot UUID to look up
        request: Command execution request with cmd, optional env, and timeout
        device_affinity: Optional affinity key for sticky device selection
        dispatcher: Injected bot command dispatcher

    Returns:
        CommandResult with exit_code, stdout, stderr, execution_time_ms

    Raises:
        HTTPException 404: Bot not found or no devices found
        HTTPException 503: No active devices available
        HTTPException 500: Internal error or PaaS error
    """
    logger.info(
        f"Executing command on bot: bot_uuid={bot_uuid}, "
        f"cmd={request.cmd[:100]!r}, "
        f"timeout={request.timeout_seconds}s, "
        f"tenant={tenant}, "
        f"device_affinity={device_affinity!r}"
    )

    try:
        result = await dispatcher.dispatch_bot_execute_command(
            bot_uuid=bot_uuid,
            cmd=request.cmd,
            tenant=tenant,
            cmd_env=request.env,
            timeout_seconds=request.timeout_seconds,
            device_affinity=device_affinity,
        )

        logger.info(
            f"Command executed on bot: bot_uuid={bot_uuid}, "
            f"exit_code={result.exit_code}, "
            f"execution_time_ms={result.execution_time_ms}"
        )

        return ApiResponse(data=result)

    except BotNotFoundError as e:
        logger.warning(f"Bot not found: {bot_uuid}")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": "BOT_NOT_FOUND",
                "message": str(e),
                "bot_uuid": bot_uuid,
            },
        )

    except NoDevicesFoundError as e:
        logger.warning(f"No devices found for bot: {bot_uuid}")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": "NO_DEVICES_FOUND",
                "message": str(e),
                "bot_uuid": bot_uuid,
            },
        )

    except NoActiveDevicesError as e:
        logger.warning(f"No active devices for bot: {bot_uuid}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": "NO_ACTIVE_DEVICES",
                "message": str(e),
                "bot_uuid": bot_uuid,
            },
        )

    except DeviceFacadeException as e:
        logger.error(f"Facade error executing command on bot {bot_uuid}: {e.message}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "error": e.original_error.code.value
                if e.original_error
                else "FACADE_ERROR",
                "message": str(e),
                "context": {
                    "operation": e.operation,
                    "platform_type": e.platform_type,
                    "paas_device_id": e.paas_device_id,
                },
            },
        )

    except Exception as e:
        logger.error(f"Unexpected error executing command on bot {bot_uuid}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "error": "INTERNAL_ERROR",
                "message": str(e),
                "bot_uuid": bot_uuid,
            },
        )
