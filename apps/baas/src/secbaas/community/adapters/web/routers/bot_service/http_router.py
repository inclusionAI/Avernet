"""Bot HTTP Proxy REST API routes.

Provides an endpoint to proxy HTTP requests to a bot's active device,
by selecting an available device and delegating to PaasServiceFacade.

Endpoint:
- ANY /api/v1/bots/{tenant}/{bot_uuid}/invoke-http/{port}/{path:path}
  - Proxy HTTP request to bot device (supports GET/POST/PUT/DELETE)
"""

import base64
from typing import Annotated

from dependency_injector.wiring import Provide, inject
from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Path,
    Query,
    Request,
    Response,
    status,
)

from secbaas.community.api.bot_runtime import (
    BotHttpDispatcher,
    BotNotFoundError,
    NoActiveDevicesError,
    NoDevicesFoundError,
)
from secbaas.community.api.device_manage import DeviceFacadeException
from secbaas.community.bootstrap import ApplicationContainer
from secbaas.community.logger import get_logger

logger = get_logger("router")

router = APIRouter(prefix="/api/v1/bots", tags=["Bot HTTP代理"])


HOP_BY_HOP_HEADERS: set[str] = {
    "connection",
    "keep-alive",
    "proxy-authorization",
    "proxy-authenticate",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
    "proxy-connection",
    "host",
}


def _filter_headers(headers: dict[str, str]) -> dict[str, str]:
    """Filter out hop-by-hop headers from request headers."""
    return {k: v for k, v in headers.items() if k.lower() not in HOP_BY_HOP_HEADERS}


@router.api_route(
    "/{tenant}/{bot_uuid}/invoke-http/{port}/{path:path}",
    methods=["GET", "POST", "PUT", "DELETE"],
    summary="Proxy HTTP request to bot device (transparent proxy)",
    description="Transparent proxy for HTTP requests to a bot's active device. Supports GET, POST, PUT, DELETE methods. Port and path are extracted from the URL.",
)
@inject
async def invoke_http_in_bot(
    tenant: Annotated[str, Path(description="Tenant for isolation")],
    bot_uuid: Annotated[str, Path(description="Bot UUID (business identifier)")],
    port: Annotated[
        int, Path(ge=1, le=65535, description="Target port on device (1-65535)")
    ],
    path: Annotated[str, Path(description="Request path forwarded to device")],
    request: Request,
    device_affinity: Annotated[
        str | None,
        Query(
            description="Device affinity key for consistent hashing-based sticky device selection"
        ),
    ] = None,
    dispatcher: BotHttpDispatcher = Depends(
        Provide[ApplicationContainer.services.bot_http_dispatcher]
    ),
) -> Response:
    """Proxy HTTP request to a bot's active device.

    Args:
        tenant: Tenant for multi-tenancy isolation
        bot_uuid: Bot UUID to look up
        port: Target port on the device container (1-65535)
        path: Request path forwarded to the device
        request: FastAPI Request object for raw access to body/headers/query
        device_affinity: Optional affinity key for sticky device selection
        dispatcher: Injected bot HTTP dispatcher

    Returns:
        Response from device with original status code, headers, body

    Raises:
        HTTPException 404: Bot not found or no devices found
        HTTPException 503: No active devices available
        HTTPException 500: Internal error or PaaS error
    """
    method = request.method
    content_type = request.headers.get("content-type", "unknown")

    logger.info(
        f"HTTP invoke in bot device: bot_uuid={bot_uuid}, "
        f"method={method}, port={port}, path={path}, "
        f"content_type={content_type}, "
        f"tenant={tenant}, "
        f"device_affinity={device_affinity!r}"
    )

    try:
        body_bytes = await request.body()

        full_path = "/" + path if not path.startswith("/") else path
        query_string = "?" + request.url.query if request.url.query else None
        filtered_headers = _filter_headers(dict(request.headers))

        result = await dispatcher.dispatch_bot_http_invoke(
            bot_uuid=bot_uuid,
            method=method,
            port=port,
            path=full_path,
            query_string=query_string,
            headers=filtered_headers,
            body=body_bytes,
            tenant=tenant,
            device_affinity=device_affinity,
        )

        body_b64 = result.get("body", "")
        response_body = base64.b64decode(body_b64) if body_b64 else b""

        logger.info(
            f"HTTP invoke resolved: bot_uuid={bot_uuid}, "
            f"status_code={result.get('status_code')}"
        )

        return Response(
            content=response_body,
            status_code=result["status_code"],
            headers=result.get("headers", {}),
        )

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
        logger.error(f"Facade error invoking HTTP for bot {bot_uuid}: {e.message}")
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
        logger.error(f"Unexpected error invoking HTTP for bot {bot_uuid}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "error": "INTERNAL_ERROR",
                "message": str(e),
                "bot_uuid": bot_uuid,
            },
        )
