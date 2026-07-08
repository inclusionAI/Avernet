"""Shared error-to-HTTP mapping helper for start-progress endpoints.

Provides ``_map_start_progress_error``, a single dispatch function used by both
the bot_uuid and publish_id start-progress routers. It consolidates the
per-exception-type HTTP response logic (status codes, error codes, detail
dicts, and headers) so that both endpoints produce consistent error shapes.

D-01: NoDevicesFoundError / NoActiveDevicesError → 503 + Retry-After: 5
D-02: BotNotFoundError → 404 with refined error_code by bot_status
D-03: DeviceFacadeException → error_code lookup table
D-04: Shared helper called by both start-progress endpoints
W-4:  publish_id propagated to all detail dicts when provided
W-2:  CONTAINER_NOT_FOUND / MACHINE_NOT_FOUND / MACHINE_NOT_CONNECTED are
      string codes used internally by the PaaS layer — the facade wraps
      them as PLATFORM_ERROR before reaching the router. Reserved for
      future facade extensions that differentiate DeviceCreationError
      sub-codes into distinct PaasError codes.
"""

from __future__ import annotations

from fastapi import HTTPException, status

from secbaas.api.bot_runtime import (
    BotNotFoundError,
    NoActiveDevicesError,
    NoDevicesFoundError,
)
from secbaas.api.device_manage import DeviceFacadeException, ErrorCode
from secbaas.logger import get_logger

logger = get_logger("router")

# ==============================================================================
# D-03: DeviceFacadeException error_code → HTTP status lookup table
#
# Only ErrorCode enum values that can actually reach the router layer are
# included.  CONTAINER_NOT_FOUND, MACHINE_NOT_FOUND, MACHINE_NOT_CONNECTED
# are **not** ErrorCode members — the facade wraps DeviceCreationError as
# PLATFORM_ERROR before it reaches the router, so those codes are unreachable
# here.  They are reserved for future facade extensions that differentiate
# DeviceCreationError sub-codes into distinct PaasError codes.
# ==============================================================================

_FACADE_ERROR_STATUS_MAP: dict[ErrorCode, int] = {
    ErrorCode.PLATFORM_ERROR: status.HTTP_501_NOT_IMPLEMENTED,
    ErrorCode.TEMPLATE_NOT_FOUND: status.HTTP_404_NOT_FOUND,
    ErrorCode.COMMAND_TIMEOUT: status.HTTP_504_GATEWAY_TIMEOUT,
}

_FACADE_ERROR_CODE_MAP: dict[ErrorCode, str] = {
    ErrorCode.PLATFORM_ERROR: "NOT_IMPLEMENTED",
    ErrorCode.TEMPLATE_NOT_FOUND: "TEMPLATE_NOT_FOUND",
    ErrorCode.COMMAND_TIMEOUT: "COMMAND_TIMEOUT",
}


def _map_start_progress_error(
    exc: Exception,
    bot_uuid: str | None = None,
    publish_id: int | None = None,
) -> HTTPException:
    """Map a domain exception to the correct HTTPException for start-progress.

    Handles 6 exception categories (D-01, D-02, D-03, W-4):

    ==========  ================================================================
    Exception   HTTP response
    ==========  ================================================================
    PublishNotFoundError   404 PUBLISH_NOT_FOUND  (only when publish_id is set)
    BotNotFoundError       404 BOT_NOT_FOUND / BOT_RELEASED / BOT_FAILED (D-02)
    NoDevicesFoundError    503 NO_DEVICES_FOUND + Retry-After: 5 (D-01)
    NoActiveDevicesError   503 NO_ACTIVE_DEVICES + Retry-After: 5 (D-01)
    DeviceFacadeException  lookup-table mapping (D-03)
    Exception (catch-all)  500 INTERNAL_ERROR
    ==========  ================================================================

    When *publish_id* is not ``None``, every detail dict includes the
    ``"publish_id"`` key (W-4).

    Args:
        exc: The domain exception caught by the router.
        bot_uuid: Bot UUID for detail dicts (may be ``None`` for
            PublishNotFoundError path).
        publish_id: Publish ID for detail dicts. When set, all detail dicts
            include ``"publish_id"``.

    Returns:
        An ``HTTPException`` ready to be raised.
    """

    # --- 1. PublishNotFoundError (only when publish_id is set) ---
    if publish_id is not None:
        # Lazy import to break circular dependency:
        # publish_manage imports router modules, so importing
        # PublishNotFoundError at module level would create a cycle.
        from secbaas.api.publish_manage import PublishNotFoundError

        if isinstance(exc, PublishNotFoundError):
            logger.warning(
                f"Publish not found for fetch_start_progress: publish_id={publish_id}"
            )
            return HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "error": "PUBLISH_NOT_FOUND",
                    "message": str(exc),
                    "publish_id": publish_id,
                },
            )

    # --- 2. BotNotFoundError → refined error_code (D-02) ---
    if isinstance(exc, BotNotFoundError):
        bot_status = exc.bot_status
        if bot_status == "RELEASED":
            error_code = "BOT_RELEASED"
        elif bot_status == "FAILED":
            error_code = "BOT_FAILED"
        else:
            error_code = "BOT_NOT_FOUND"

        detail: dict = {
            "error": error_code,
            "message": str(exc),
            "bot_uuid": bot_uuid,
        }
        if publish_id is not None:
            detail["publish_id"] = publish_id

        logger.warning(
            f"Bot not found for fetch_start_progress: bot_uuid={bot_uuid}, "
            f"error_code={error_code}"
        )
        return HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=detail,
        )

    # --- 3. NoDevicesFoundError → 503 + Retry-After (D-01) ---
    if isinstance(exc, NoDevicesFoundError):
        detail = {
            "error": "NO_DEVICES_FOUND",
            "message": str(exc),
            "bot_uuid": bot_uuid,
        }
        if publish_id is not None:
            detail["publish_id"] = publish_id

        logger.warning(
            f"No devices found for fetch_start_progress: bot_uuid={bot_uuid}"
        )
        return HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=detail,
            headers={"Retry-After": "5"},
        )

    # --- 4. NoActiveDevicesError → 503 + Retry-After (D-01) ---
    if isinstance(exc, NoActiveDevicesError):
        detail = {
            "error": "NO_ACTIVE_DEVICES",
            "message": str(exc),
            "bot_uuid": bot_uuid,
        }
        if publish_id is not None:
            detail["publish_id"] = publish_id

        logger.warning(
            f"No active devices for fetch_start_progress: bot_uuid={bot_uuid}"
        )
        return HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=detail,
            headers={"Retry-After": "5"},
        )

    # --- 5. DeviceFacadeException → lookup table (D-03) ---
    if isinstance(exc, DeviceFacadeException):
        logger.error(f"Facade error fetching start-progress: {exc.message}")

        if exc.original_error is not None:
            code = exc.original_error.code
            http_status_code = _FACADE_ERROR_STATUS_MAP.get(
                code, status.HTTP_500_INTERNAL_SERVER_ERROR
            )
            error_code = _FACADE_ERROR_CODE_MAP.get(code, code.value)
        else:
            http_status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
            error_code = "FACADE_ERROR"

        detail = {
            "error": error_code,
            "message": str(exc),
            "context": {
                "operation": exc.operation,
                "platform_type": exc.platform_type,
                "paas_device_id": exc.paas_device_id,
            },
        }
        if publish_id is not None:
            detail["publish_id"] = publish_id

        return HTTPException(
            status_code=http_status_code,
            detail=detail,
        )

    # --- 6. Catch-all → 500 INTERNAL_ERROR ---
    logger.error(
        f"Unexpected error fetching start-progress: "
        f"bot_uuid={bot_uuid}, publish_id={publish_id}, exc={exc}"
    )
    detail = {
        "error": "INTERNAL_ERROR",
        "message": str(exc),
        "bot_uuid": bot_uuid,
    }
    if publish_id is not None:
        detail["publish_id"] = publish_id

    return HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail=detail,
    )
