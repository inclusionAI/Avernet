"""Internal router for cross-instance request forwarding.

Provides endpoints for receiving forwarded requests from other secbaas instances.
This enables distributed deployment where requests can be routed to the
instance managing the target machine.

Cross-process awareness (regression fix): the receiving instance may run
multiple worker processes; the mng WebSocket for ``machine_id`` is owned by
exactly one of them. Before this fix, ``internal_forward`` only checked the
current process's ``ConnectionManager``, so a forward that landed on a worker
that did not hold the connection always returned ``MACHINE_NOT_CONNECTED`` —
even when a sibling worker on the same instance was holding it. The receiver
now delegates to ``LocalPaasService.dispatch_to_local_connection`` which
encapsulates the full same-instance decision tree (this-process → UDS
forward → race fallthrough → MACHINE_NOT_CONNECTED), shared with
``LocalPaasService._route_command`` to keep the two paths from drifting.
"""

import socket

from dependency_injector.wiring import inject
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from secbaas.community.api.device_manage import LocalPaasService
from secbaas.community.bootstrap import ApplicationContainer, Provide
from secbaas.community.logger import get_logger

logger = get_logger("router")

# Lazy-initialized server IP to avoid blocking DNS lookup at import time
_SERVER_IP: str | None = None


def _get_server_ip() -> str:
    """Get server IP address, lazily cached to avoid blocking at import."""
    global _SERVER_IP
    if _SERVER_IP is None:
        _SERVER_IP = socket.gethostbyname(socket.gethostname())
    return _SERVER_IP


router = APIRouter(prefix="/internal", tags=["internal"])


class ForwardRequest(BaseModel):
    """Request body for internal forward endpoint.

    Per PHS01: JSON-RPC style with action, machine_id, params, request_id.
    machine_id is BaaS internal routing metadata, not passed to mng.
    """

    action: str = Field(
        ..., description="The action to execute (e.g., execute_command)"
    )
    machine_id: str = Field(
        ..., description="Target machine ID for BaaS internal routing"
    )
    params: dict = Field(..., description="Action parameters (forwarded to mng daemon)")
    request_id: str = Field(..., description="Unique request ID for correlation")


@router.post("/v1/forward")
@inject
async def internal_forward(
    request: ForwardRequest,
    http_request: Request,
    local_paas_service: LocalPaasService = Depends(
        Provide[ApplicationContainer.services.dispatcher_local_paas_service]
    ),
) -> dict:
    """Receive forwarded request from another secbaas instance.

    This endpoint receives HTTP POST requests from other secbaas instances
    that need to forward commands to mng daemons connected to this instance.

    Request Flow:
    1. Parse JSON body (action, machine_id, params, request_id)
    2. Validate machine_id (required for routing)
    3. Delegate to ``LocalPaasService.dispatch_to_local_connection`` which
       resolves the connection across all workers on this instance
       (current process → UDS-forward to sibling worker → MACHINE_NOT_CONNECTED).
    4. Return the dispatcher's dict verbatim — success returns the raw mng
       payload; error envelopes ``{status:"error", error:..., message:..., data:...}``
       align with this endpoint's pre-existing error response shape.

    Args:
        request: The forwarded request containing action, params, request_id.
        http_request: The HTTP request (used for header access like X-Request-ID).

    Returns:
        Dict with response data. Success returns the raw mng payload (e.g.
        ``{"status": "ok", "data": {...}}``). Errors return
        ``{"status": "error", "error": "CODE", "message": "...", "data": {...}}``.

    HTTP Status Codes:
        200: Command executed (or error envelope returned)
        400: Invalid request (missing machine_id)
    """
    # Extract X-Request-ID for tracing (if present)
    x_request_id = http_request.headers.get("X-Request-ID")
    if x_request_id:
        logger.info(
            f"[INTERNAL-FORWARD] Received forward request: "
            f"action={request.action}, request_id={request.request_id}, "
            f"x_request_id={x_request_id}"
        )
    else:
        logger.info(
            f"[INTERNAL-FORWARD] Received forward request: "
            f"action={request.action}, request_id={request.request_id}"
        )

    # Extract machine_id from request (required for routing to correct connection)
    machine_id = request.machine_id
    if not machine_id:
        logger.error("[INTERNAL-FORWARD] Missing machine_id in request")
        raise HTTPException(status_code=400, detail="machine_id required")

    # Build command payload for mng daemon. ``request_id`` is also threaded
    # through ``dispatch_to_local_connection``'s ``request_id`` argument so
    # the same-process path uses ``send_command_with_request_id`` (preserving
    # the upstream-supplied correlation id), AND the cross-process path's
    # ``command["request_id"]`` seeding picks it up.
    command = {
        "action": request.action,
        "params": request.params,
        "request_id": request.request_id,
    }

    try:
        result = await local_paas_service.dispatch_to_local_connection(
            machine_id=machine_id,
            command=command,
            request_id=request.request_id,
        )

        # Distinguish error envelopes from success for logging fidelity —
        # the response shape is unchanged either way (caller-side contract).
        if isinstance(result, dict) and result.get("status") == "error":
            logger.warning(
                f"[INTERNAL-FORWARD] Dispatch returned error envelope: "
                f"action={request.action}, machine_id={machine_id}, "
                f"request_id={request.request_id}, "
                f"error={result.get('error')}, message={result.get('message')}"
            )
        else:
            logger.info(
                f"[INTERNAL-FORWARD] Command completed successfully: "
                f"action={request.action}, machine_id={machine_id}, "
                f"request_id={request.request_id}"
            )
        return result

    except TimeoutError:
        # Same-process path lets ConnectionManager exceptions propagate
        # (the dispatcher only catches errors on the cross-process path —
        # that's the "异常只在外层包装" contract). Mirror the pre-refactor
        # internal_forward error-dict shape so upstream callers see no
        # behavioural drift on the timeout / lost-connection paths.
        logger.error(
            f"[INTERNAL-FORWARD] Command timeout: "
            f"action={request.action}, machine_id={machine_id}, "
            f"request_id={request.request_id}"
        )
        return {
            "status": "error",
            "error": "COMMAND_TIMEOUT",
            "message": f"Command timeout for machine {machine_id}",
            "data": {
                "machine_id": machine_id,
                "action": request.action,
                "request_id": request.request_id,
            },
        }

    except ConnectionError as e:
        logger.error(
            f"[INTERNAL-FORWARD] Connection lost: "
            f"action={request.action}, machine_id={machine_id}, "
            f"request_id={request.request_id}, error={e}"
        )
        return {
            "status": "error",
            "error": "CONNECTION_LOST",
            "message": f"Connection lost for machine {machine_id}: {e}",
            "data": {
                "machine_id": machine_id,
                "action": request.action,
                "request_id": request.request_id,
                "error": str(e),
            },
        }

    except Exception as e:
        logger.error(
            f"[INTERNAL-FORWARD] Unexpected error: "
            f"action={request.action}, machine_id={machine_id}, "
            f"request_id={request.request_id}, error={type(e).__name__}: {e}"
        )
        return {
            "status": "error",
            "error": "INTERNAL_ERROR",
            "message": f"Failed to execute command: {e}",
            "data": {
                "machine_id": machine_id,
                "action": request.action,
                "request_id": request.request_id,
                "exception": type(e).__name__,
                "error": str(e),
            },
        }
