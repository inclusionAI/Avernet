"""Local management WebSocket endpoint for mng daemon connections.

Provides unified WebSocket endpoint at /ws/local/management with full
ConnectionManager integration for registration, heartbeat handling, and
disconnect management per D-W01~05, D-M01~03, D-D01~02, D-DC01~04.

Endpoints:
- WS /ws/local/management - Unified WebSocket endpoint with ConnectionManager

Query Parameters:
- machine_id (required): Unique machine identifier from mng daemon
- machine_name (optional): Human-readable machine name

Authentication:
- Via JWT Bearer token from Authorization header (per Phase 18.4)
- Token payload parsed for "sno" field as user_id

Message Format (JSON):
- type: "heartbeat" | "result" | "callback" (per D-MP02)
- payload: {...} (optional, for future use)

Heartbeat (D-HB01~04):
- {"type": "heartbeat"} - minimal format per D-HB02
- Updates last_heartbeat via ConnectionManager.update_heartbeat
- No response sent per D-HB03 (fire-and-forget)
"""

import asyncio
import base64
import json
from datetime import datetime
from typing import TYPE_CHECKING, Annotated

if TYPE_CHECKING:
    from secbaas.community.api.device_manage import LocalPaasService
    from secbaas.community.api.paas import ConnectionManager, PaasServiceFactory

from dependency_injector.wiring import Provide, inject
from fastapi import (
    APIRouter,
    Depends,
    Query,
    WebSocket,
    WebSocketDisconnect,
    WebSocketException,
    status,
)

from secbaas.community.bootstrap import ApplicationContainer
from secbaas.community.logger import get_logger

logger = get_logger("local_management_ws")


router = APIRouter(tags=["Local Management WebSocket"])


def _extract_user_id_from_jwt(websocket: WebSocket) -> str:
    """Extract user_id from JWT Bearer token in Authorization header.

    JWT payload is parsed without signature verification per D-01.
    Extracts 'sno' field as user_id string per D-07.

    Args:
        websocket: FastAPI WebSocket connection object.

    Returns:
        User ID string extracted from JWT "sno" field.

    Raises:
        WebSocketException: With code 1008 for any auth failure.
    """
    client_ip = websocket.client.host if websocket.client else "unknown"
    url = str(websocket.url) if websocket.scope else "unknown"
    query_params = dict(websocket.query_params) if websocket.query_params else {}

    logger.info(
        f"[JWT] Starting auth extraction from {client_ip}, URL={url}, "
        f"query_params={query_params}, headers_keys={list(websocket.headers.keys())}"
    )

    auth_header = websocket.headers.get("Authorization")

    if not auth_header:
        logger.warning(
            f"[JWT] Missing Authorization header from {client_ip}, "
            f"URL={url}, available_headers={dict(websocket.headers)}"
        )
        raise WebSocketException(
            code=status.WS_1008_POLICY_VIOLATION,
            reason="Missing Authorization header",
        )

    parts = auth_header.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        logger.warning(f"Invalid authorization format from {client_ip}")
        raise WebSocketException(
            code=status.WS_1008_POLICY_VIOLATION,
            reason="Invalid authorization format",
        )

    token = parts[1]

    try:
        # JWT format: header.payload.signature
        parts = token.split(".")
        if len(parts) != 3:
            logger.warning(
                f"[JWT] Invalid JWT format from {client_ip}: expected 3 parts, got {len(parts)}, "
                f"token_preview={token[:20]}..."
            )
            raise ValueError("Invalid JWT format")

        # Base64 decode payload with padding fix per D-02
        payload_b64 = parts[1]
        padding = 4 - len(payload_b64) % 4
        if padding != 4:
            payload_b64 += "=" * padding
        payload_json = base64.urlsafe_b64decode(payload_b64).decode()
        payload = json.loads(payload_json)

        logger.debug(f"[JWT] Decoded payload from {client_ip}: {payload}")

        sno = payload.get("sno")
        if sno is None or sno == "":
            logger.warning(
                f"[JWT] Missing sno field in JWT from {client_ip}, "
                f"available_fields={list(payload.keys())}"
            )
            raise WebSocketException(
                code=status.WS_1008_POLICY_VIOLATION,
                reason="Missing sno field",
            )

        logger.info(f"[JWT] Successfully extracted user_id={sno} from {client_ip}")
        return str(sno)

    except (ValueError, json.JSONDecodeError) as e:
        logger.warning(
            f"[JWT] Parse error from {client_ip}: {e}, token_preview={token[:50]}..."
        )
        raise WebSocketException(
            code=status.WS_1008_POLICY_VIOLATION,
            reason="Invalid JWT format",
        )


async def _handle_callback_fire_and_forget(
    machine_id: str,
    action: str,
    payload: dict,
    local_service: "LocalPaasService",
) -> None:
    """Handle callback message in fire-and-forget mode (no response needed).

    Per 25-CONTEXT.md Decision 3: All errors logged at WARNING with full context.
    Per 25-CONTEXT.md Decision 4: Uses asyncio.create_task() without task tracking.

    Args:
        machine_id: Machine identifier from the callback message.
        action: Callback action name (e.g., "container_ready").
        payload: Full callback payload dict.
        local_service: LocalPaasService instance for business logic.
    """
    try:
        params = payload.get("params", {})
        await local_service.handle_callback(machine_id, action, params)
        logger.info(
            f"[CALLBACK_FIRE_FORGET] action={action} machine={machine_id} completed"
        )
    except Exception as e:
        logger.warning(
            f"[CALLBACK_ERROR] Fire-and-forget callback failed: "
            f"action={action}, machine_id={machine_id}, "
            f"error_type={type(e).__name__}, error={e}",
            exc_info=True,
        )


async def _handle_callback_with_response(
    machine_id: str,
    request_id: str,
    action: str,
    payload: dict,
    local_service: "LocalPaasService",
    connection_manager: "ConnectionManager",
) -> None:
    """Handle callback message in request-response mode (send result back).

    Per 25-CONTEXT.md Decision 3: All errors logged at WARNING with full context.
    Per 25-CONTEXT.md Decision 4: Uses asyncio.create_task() without task tracking.

    Args:
        machine_id: Machine identifier from the callback message.
        request_id: Request ID for correlation with the callback invocation.
        action: Callback action name.
        payload: Full callback payload dict.
        local_service: LocalPaasService instance for business logic.
    """
    try:
        params = payload.get("params", {})
        result = await local_service.handle_callback(machine_id, action, params)

        if result is not None:
            status = result.get("status", "ok")
            if status == "ok":
                await connection_manager.send_callback_result(
                    machine_id,
                    request_id,
                    status="ok",
                    data=result.get("data"),
                )
            else:
                await connection_manager.send_callback_result(
                    machine_id,
                    request_id,
                    status="error",
                    error=result.get("error"),
                    message=result.get("message"),
                )
        else:
            # No result from callback - send empty success response
            await connection_manager.send_callback_result(
                machine_id, request_id, status="ok", data={}
            )

        logger.info(
            f"[CALLBACK_WITH_RESPONSE] action={action} machine={machine_id} "
            f"request_id={request_id} completed"
        )
    except Exception as e:
        logger.warning(
            f"[CALLBACK_ERROR] Callback with response failed: "
            f"action={action}, machine_id={machine_id}, request_id={request_id}, "
            f"error_type={type(e).__name__}, error={e}",
            exc_info=True,
        )
        # Send error response to mng daemon
        success = await connection_manager.send_callback_result(
            machine_id,
            request_id,
            status="error",
            error="CALLBACK_PROCESSING_ERROR",
            message=str(e),
        )
        if not success:
            logger.error(
                f"[CALLBACK_ERROR_RESPONSE_FAILED] Could not send error response to mng daemon: "
                f"machine_id={machine_id}, request_id={request_id}"
            )


@router.websocket("/ws/local/management")
@inject
async def local_management_websocket(
    websocket: WebSocket,
    machine_id: Annotated[
        str, Query(description="Unique machine identifier (required)")
    ],
    machine_name: Annotated[
        str | None, Query(description="Human-readable machine name (optional)")
    ] = None,
    connection_manager: "ConnectionManager" = Depends(
        Provide[ApplicationContainer.services.connection_management]
    ),
    paas_service_factory: "PaasServiceFactory" = Depends(
        Provide[ApplicationContainer.services.paas_service_factory]
    ),
) -> None:
    """WebSocket endpoint for mng daemon management with ConnectionManager.

    Unified endpoint at /ws/local/management per Phase 17.
    Handles registration, heartbeat (via ConnectionManager), result routing,
    and disconnect cleanup with database integration.

    Args:
        websocket: FastAPI WebSocket connection object.
        machine_id: Required query param - unique machine identifier.
        machine_name: Optional query param - human-readable name.
        connection_manager: Injected ConnectionManager singleton for connection
            lifecycle, heartbeat, and route_info management.

    Authentication:
        - Via JWT Bearer token from Authorization header (per Phase 18.4)
        - Token payload parsed for "sno" field as user_id

    Flow:
        1. Extract user_id from JWT via _extract_user_id_from_jwt
        2. Validate machine_id pre-accept (raise WebSocketException 1008 if missing)
        3. Check capacity (D-CPL) and duplicate connection (D-DC01~04)
        4. Accept WebSocket connection
        5. Register with ConnectionManager
        6. Call ConnectionManager.on_connect() for DB updates (D-DB03)
        7. Call handle_mng_register() for service layer
        8. Message loop: heartbeat -> ConnectionManager, result -> ConnectionManager
        9. On disconnect: DB update via on_disconnect(), then cleanup

    Raises:
        WebSocketException: With code 1013 (Service Unavailable) when at capacity;
            code 1008 (Policy Violation) for missing machine_id,
            duplicate connection, or authentication failure.
        WebSocketDisconnect: On client disconnect (handled gracefully).
    """
    client_ip = websocket.client.host if websocket.client else "unknown"
    url = str(websocket.url) if websocket.scope else "unknown"
    query_params = dict(websocket.query_params) if websocket.query_params else {}

    logger.info(
        f"[WS_CONNECT] New connection from {client_ip}, URL={url}, "
        f"query_params={query_params}, machine_id_param={machine_id!r}, "
        f"machine_name_param={machine_name!r}, "
        f"user_agent={websocket.headers.get('user-agent', 'unknown')!r}"
    )

    user_id = _extract_user_id_from_jwt(websocket)
    logger.info(
        f"[WS_AUTH] User authenticated: user_id={user_id}, "
        f"client_ip={client_ip}, machine_id={machine_id!r}"
    )

    # Pre-accept validation: machine_id is required per D-W03
    if not machine_id:
        logger.warning(
            f"[WS_REJECT] Missing machine_id from {client_ip}, user_id={user_id}, "
            f"query_params={query_params}"
        )
        raise WebSocketException(
            code=status.WS_1008_POLICY_VIOLATION,
            reason="machine_id is required",
        )

    # D-CPL: Capacity check before accepting
    max_connections = connection_manager.MAX_CONNECTIONS
    if connection_manager.is_at_capacity():
        logger.warning(
            f"[WS_REJECT] Server at capacity from {client_ip}, user_id={user_id}, "
            f"max={max_connections}"
        )
        raise WebSocketException(
            code=status.WS_1013_TRY_AGAIN_LATER,
            reason="Server at connection capacity",
        )
    logger.info(f"[WS_CAPACITY] Capacity check passed: max={max_connections}")

    # Duplicate connection check with cross-user validation per D-DC01~04
    if connection_manager.is_connected(machine_id):
        existing_user_id = connection_manager._get_user_id(machine_id)
        if existing_user_id and existing_user_id != user_id:
            # D-DC04: Cross-user machine hijacking attempt
            logger.warning(
                f"[WS_REJECT] Cross-user machine attempt: machine_id={machine_id}, "
                f"existing_user={existing_user_id}, new_user={user_id}, "
                f"client_ip={client_ip}"
            )
        else:
            logger.warning(
                f"[WS_REJECT] Duplicate connection: machine_id={machine_id}, "
                f"user_id={user_id}, client_ip={client_ip}"
            )
        raise WebSocketException(
            code=status.WS_1008_POLICY_VIOLATION,
            reason="Machine already connected",
        )
    logger.info(f"[WS_DUPLICATE] Duplicate check passed for machine_id={machine_id}")

    # Accept connection
    logger.info(
        f"[WS_ACCEPT] Accepting connection for machine_id={machine_id}, user_id={user_id}"
    )
    await websocket.accept()

    # Build connection metadata per D-CM01 (filter sensitive headers)
    all_headers = dict(websocket.headers)
    safe_headers = {
        k: v
        for k, v in all_headers.items()
        if k.lower() not in {"authorization", "cookie", "x-auth-token"}
    }
    metadata = {
        "remote_addr": websocket.client.host if websocket.client else None,
        "user_agent": websocket.headers.get("user-agent"),
        "headers": safe_headers,
        "connected_at": datetime.now().isoformat(),
        "user_id": user_id,
    }

    # Create service using factory
    local_service = paas_service_factory.create_local_paas_service(
        user_id=user_id,
        machine_id=machine_id,
    )

    try:
        # Register with ConnectionManager (in-memory tracking)
        logger.info(f"[WS_REGISTER] Adding connection for machine_id={machine_id}")
        await connection_manager._add_connection(machine_id, websocket, metadata)

        # D-DB03: Database updates for connection (instance, status=ONLINE)
        logger.info(f"[WS_DB] Updating DB status to ONLINE for machine_id={machine_id}")
        connection_manager._on_connect(machine_id, user_id)

        # Handle service layer registration per D-D01
        logger.info(
            f"[WS_SERVICE] Calling handle_mng_register for machine_id={machine_id}"
        )
        await local_service.handle_mng_register(
            machine_id=machine_id,
            user_id=user_id,
            machine_name=machine_name,
        )
        logger.info(
            f"[WS_REGISTERED] Mng fully registered: machine_id={machine_id}, user_id={user_id}"
        )

        # Message loop
        logger.info(f"[WS_LOOP] Starting message loop for machine_id={machine_id}")
        msg_count = 0
        while True:
            try:
                raw_message = await websocket.receive_text()
                msg_count += 1
                logger.debug(
                    f"[WS_MSG] Received message #{msg_count} from {machine_id}: {raw_message[:200]}..."
                )

                try:
                    message = json.loads(raw_message)
                except json.JSONDecodeError as e:
                    logger.error(
                        f"[WS_ERR] Invalid JSON from {machine_id}: {e}, "
                        f"raw_message={raw_message[:200]}..."
                    )
                    # Continue - don't close connection for single bad message
                    continue

                msg_type = message.get("type", "")

                if msg_type == "heartbeat":
                    # D-HB01~04: Update heartbeat via ConnectionManager
                    await connection_manager._update_heartbeat(machine_id)

                    # D-HB05~06: Process optional bot_list from heartbeat payload
                    payload = message.get("payload", {})
                    bot_list = payload.get("bot_list", [])
                    if bot_list:
                        logger.info(
                            f"[WS_HEARTBEAT] Heartbeat contains {len(bot_list)} "
                            f"container(s), delegating to service"
                        )
                        user_id = connection_manager._get_user_id(machine_id)
                        if user_id:
                            # WR-03: attach a done-callback that surfaces
                            # exceptions from the fire-and-forget heartbeat
                            # handler. Without this, a failure in
                            # `handle_heartbeat_containers` setup (before its
                            # per-container try/except loop) disappears as
                            # `Task exception was never retrieved`. Mirrors the
                            # `_destroy_orphan_with_logging` pattern in
                            # `local_paas_service.py`.
                            heartbeat_task = asyncio.create_task(
                                local_service.handle_heartbeat_containers(
                                    machine_id, user_id, bot_list
                                )
                            )

                            def _log_heartbeat_exc(
                                task: asyncio.Task,
                                _machine_id: str = machine_id,
                            ) -> None:
                                if task.cancelled():
                                    return
                                exc = task.exception()
                                if exc is not None:
                                    logger.error(
                                        f"[WS_HEARTBEAT_TASK_FAIL] "
                                        f"machine_id={_machine_id}: "
                                        f"{type(exc).__name__}: {exc}",
                                        exc_info=exc,
                                    )

                            heartbeat_task.add_done_callback(_log_heartbeat_exc)
                        else:
                            logger.warning(
                                f"[WS_HEARTBEAT] Cannot process bot_list: user_id not "
                                f"found for machine {machine_id}"
                            )

                    logger.debug(
                        f"[WS_HEARTBEAT] Heartbeat #{msg_count} from {machine_id}"
                    )
                    # NO heartbeat_ack sent per D-HB03 (fire-and-forget)

                elif msg_type == "result":
                    # R3.7: Route result to pending request via ConnectionManager
                    connection_manager._handle_result(message)
                    logger.debug(
                        f"[WS_RESULT] Result message #{msg_count} routed for {machine_id}"
                    )

                elif msg_type == "callback":
                    # Per 25-CONTEXT.md: Handle callback messages from mng daemon
                    payload = message.get("payload", {})
                    action = payload.get("action", "unknown")
                    request_id = message.get("request_id")

                    logger.info(
                        f"[WS_CALLBACK] Received callback from {machine_id}: "
                        f"action={action}, has_request_id={request_id is not None}"
                    )

                    if request_id:
                        # Request-response mode: send result back to mng
                        asyncio.create_task(
                            _handle_callback_with_response(
                                machine_id,
                                request_id,
                                action,
                                payload,
                                local_service,
                                connection_manager,
                            )
                        )
                    else:
                        # Fire-and-forget mode: no response needed
                        asyncio.create_task(
                            _handle_callback_fire_and_forget(
                                machine_id,
                                action,
                                payload,
                                local_service,
                            )
                        )

                else:
                    logger.warning(
                        f"[WS_UNKNOWN] Unknown message type from {machine_id}: {msg_type}, "
                        f"message={message}"
                    )

            except WebSocketDisconnect as e:
                logger.info(
                    f"[WS_DISCONNECT] WebSocket disconnected: machine_id={machine_id}, "
                    f"code={e.code}, reason={e.reason!r}, total_msgs={msg_count}"
                )
                return

    except Exception as e:
        logger.error(
            f"[WS_EXCEPTION] Unexpected error for machine_id={machine_id}: {type(e).__name__}: {e}",
            exc_info=True,
        )
        raise

    finally:
        # D-DB05: Database update for disconnect (status=OFFLINE) before cleanup
        logger.info(f"[WS_CLEANUP] Cleaning up for machine_id={machine_id}")
        try:
            connection_manager._on_disconnect(machine_id)
            logger.info(f"[WS_DB] DB status updated to OFFLINE for {machine_id}")
        except Exception as e:
            logger.warning(f"[WS_ERR] Error during on_disconnect for {machine_id}: {e}")

        # Guaranteed cleanup per D-EH05
        try:
            connection_manager._remove_connection(machine_id)
            logger.info(f"[WS_CLEANUP] Removed connection for {machine_id}")
        except Exception as e:
            logger.warning(f"[WS_ERR] Error removing connection for {machine_id}: {e}")

        try:
            await local_service.handle_mng_disconnect(machine_id)
            logger.info(f"[WS_SERVICE] Service disconnect handled for {machine_id}")
        except Exception as e:
            logger.warning(
                f"[WS_ERR] Error during service disconnect for {machine_id}: {e}"
            )

        logger.info(
            f"[WS_DISCONNECTED] Mng fully disconnected: machine_id={machine_id}, status=OFFLINE"
        )
