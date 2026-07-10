"""Local PaaS service adapter implementation for Docker-based local devices.

Provides LocalPaasService that implements the PaasService ABC for local Docker
containers via WebSocket communication with mng daemon.

Per Decision D-L01: Dependencies are CM + Repo + IR (ConnectionManager,
LocalUserMachineRepository, InstanceRouter). Per Decision D-L02: WebSocket layer
calls LocalPaasService directly.

Lifecycle methods (create, destroy, restart, update, execute, query) are
fully implemented via mng daemon WebSocket communication.
"""

from __future__ import annotations

import asyncio
import base64
import os
import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from secbaas.community.api.bot_manage import FetchStartProgressResult
from secbaas.community.api.bot_runtime import HttpConnectionInfo, WsConnectionInfo
from secbaas.community.api.device_manage import (
    CommandResult,
    DeviceCreateConfig,
    DeviceCreationError,
    DeviceCreationResult,
    DeviceInfo,
    DeviceStatus,
    LocalCreateConfig,
    LocalCreationResult,
    LocalCredentials,
    LocalDeviceId,
    LocalDeviceInfo,
)
from secbaas.community.api.device_manage import (
    LocalPaasService as LocalPaasServiceProtocol,
)
from secbaas.community.api.tenant_manage import TenantType
from secbaas.community.core.repository.device import DeviceRepository
from secbaas.community.core.repository.local_user_machine import (
    LocalUserMachineRecord,
    LocalUserMachineRepository,
)
from secbaas.community.core.repository.publish_record import (
    PublishRecordRepository,
)
from secbaas.community.core.service.paas.desktop.worker_router import (
    RouteNotFoundError,
    WorkerOfflineError,
)
from secbaas.community.core.utils.env_utils import get_current_env
from secbaas.community.logger import get_logger
from secbaas.community.spi.sandbox.desktop import SandboxPluginError

from ._paas_service import PaasService

logger = get_logger("core-service")


def _normalize_message(raw_message: str | list | Any) -> str:
    """Normalize message field to string.

    Handles cases where message is a list of dicts (from WebSocket error responses)
    or a plain string.

    Args:
        raw_message: Raw message from result (str, list, or other)

    Returns:
        Normalized string message
    """
    if isinstance(raw_message, list):
        parts = []
        for item in raw_message:
            if isinstance(item, dict) and "message" in item:
                parts.append(str(item["message"]))
            else:
                parts.append(str(item))
        return "; ".join(parts)
    return str(raw_message)


if TYPE_CHECKING:
    from secbaas.community.api.device_manage import OutBoundOperationRule

    # Phase 34: DeviceRecord forward reference for _process_publish_callback_for_device
    from secbaas.community.core.repository.device import DeviceRecord

    # Phase 18.5: Add DeviceTemplateRepository for type hints
    from secbaas.community.core.repository.device_template import (
        DeviceTemplateRepository,
    )

    # Phase 65.1: WsRelaySessionRepository for init-row pre-creation
    from secbaas.community.core.repository.ws_relay_session import (
        WsRelaySessionRepository,
    )

    # Forward references for components defined in Plan 15.1-03
    from secbaas.community.core.service.paas.desktop import (
        ConnectionManager,
        InstanceRouter,
    )
    from secbaas.community.spi.sandbox.desktop import (
        DesktopSandboxPlugin,
    )


class LocalPaasService(PaasService, LocalPaasServiceProtocol):
    """Local platform PaaS service implementation.

    Orchestrates device lifecycle operations (create, destroy, execute) with routing
    logic for same-instance (via ConnectionManager) vs cross-instance (via InstanceRouter)
    command dispatch.

    Architectural dependency (per D-15.3-01, D-15.3-02):
        - Uses ConnectionManager public API: send_command(), send_command_with_request_id(), is_connected()
        - Does NOT manage connection lifecycle (add/remove handled by WebSocket layer)
        - Owns routing decision: ConnectionManager for same-instance, InstanceRouter for cross-instance

    Per D-L01: Accepts all dependencies via constructor:
    - credentials: LocalCredentials for user/machine identification
    - repository: LocalUserMachineRepository for user-machine persistence
    - connection_manager: ConnectionManager for WebSocket session tracking
    - instance_router: InstanceRouter for cross-instance forwarding
    - server_ip: This secbaas server's IP address for routing
    """

    # Default WebSocket connection mode.
    # "direct" = ws://localhost (same-machine only, original behaviour).
    # "relay"  = agentclawproxy + open_ws_relay (same + cross-machine).
    # Override per-instance via the ws_conn_mode constructor parameter or
    # the _LOCAL_WS_CONN_MODE module constant in _factory.py.
    _DEFAULT_WS_CONN_MODE: str = "direct"

    def __init__(
        self,
        credentials: LocalCredentials,
        repository: LocalUserMachineRepository,
        connection_manager: ConnectionManager,
        instance_router: InstanceRouter,
        server_ip: str,
        desktop_sandbox_plugin: DesktopSandboxPlugin,
        env: str | None = None,
        device_template_repository: DeviceTemplateRepository
        | None = None,  # Add per D-04
        device_repository: DeviceRepository
        | None = None,  # Phase 26: for callback handling
        publish_record_repository: PublishRecordRepository
        | None = None,  # Phase 26: for callback handling
        worker_router: Any | None = None,  # Phase 31: New parameter
        relay_repository: WsRelaySessionRepository
        | None = None,  # Phase 65.1: for init-row pre-creation in relay flow
        ws_conn_mode: str
        | None = None,  # Phase 66: "direct" | "relay" (None → class default)
    ) -> None:
        """Initialize LocalPaasService with all required dependencies.

        Args:
            credentials: LocalCredentials containing template_id, template_uuid,
                tenant_name (runtime params from merged config).
            repository: LocalUserMachineRepository for baas_local_user_machine
                table operations.
            connection_manager: ConnectionManager for WebSocket session state
                tracking (forward reference - type hint only).
            instance_router: InstanceRouter for cross-instance HTTP forwarding
                (forward reference - type hint only).
            server_ip: This secbaas server's IP address for routing.
            env: Environment identifier (dev, pre, prod). If None, derived from
                environment variables using get_current_env().
            device_repository: DeviceRepository for baas_device table operations.
                Required for Phase 26 callback handling.
            publish_record_repository: PublishRecordRepository for baas_publish_record
                table operations. Required for Phase 26 callback handling.
            worker_router: Optional WorkerRouter for cross-process forwarding (Phase 31).

        Raises:
            ValueError: If credentials is None. Type correctness of
                ``credentials`` is enforced by the type annotation
                (``LocalCredentials``); runtime field validation of
                machine_id / bot_id is deferred to ``create_device()``.
        """
        if credentials is None:
            raise ValueError("credentials is required")
        # Per D-FF02: Runtime parameters (machine_id, bot_id) come from merged config
        # via create_device(), not from template-stored credentials.
        # Validation of those fields happens in create_device() method.

        self._credentials = credentials
        self._repository = repository
        self._connection_manager = connection_manager
        self._instance_router = instance_router
        self._server_ip = server_ip
        self._env = env if env is not None else get_current_env()
        # Phase 18.5: DeviceTemplateRepository for default template query
        self._device_template_repository = device_template_repository
        # Phase 26: Repositories for callback handling
        self._device_repository = device_repository
        self._publish_record_repository = publish_record_repository
        # Phase 31: WorkerRouter for cross-process routing (Phase 32 implements forwarding)
        self._worker_router = worker_router
        # Phase 65.1: WsRelaySessionRepository for init-row pre-creation in relay flow
        self._relay_repository = relay_repository
        # desktop_sandbox_plugin: used in resolve_ws_conn_info() (Phase 66)
        # to delegate token/target/ws_url/expires_at construction to the
        # Plugin layer (per D-02 mixed mode).
        self._desktop_sandbox_plugin = desktop_sandbox_plugin
        # Phase 66: ws_conn_mode — "direct" (localhost, same-machine only) or
        # "relay" (agentclawproxy + open_ws_relay, same + cross-machine).
        # Defaults to the class-level _DEFAULT_WS_CONN_MODE.
        self._ws_conn_mode = (
            ws_conn_mode if ws_conn_mode is not None else self._DEFAULT_WS_CONN_MODE
        )

    async def get_credentials(self) -> LocalCredentials:
        """Get the credentials used by this service instance.

        Returns:
            LocalCredentials instance containing template_id, template_uuid,
            tenant_name (per D-FF02: runtime params from merged config).
        """
        return self._credentials

    async def get_platform_type(self) -> TenantType:
        """Return Local platform type."""
        return TenantType.LOCAL

    def _get_default_local_template_id(self) -> int:
        """Get the default Local template ID for machine registration.

        Queries the DeviceTemplateRepository for the minimum template_id of
        type='Local', status='ONLINE', non-deleted templates.

        Returns:
            Minimum Local template ID (int).

        Raises:
            DeviceCreationError: When the DeviceTemplateRepository was not wired
                or no Local template is configured for the current env. WR-05:
                the previous behaviour of returning ``0`` as a "safe fallback"
                violated FK/NOT-NULL contracts on ``baas_local_user_machine``;
                callers (e.g., ``handle_mng_register``) now fail fast with
                ``LOCAL_TEMPLATE_NOT_CONFIGURED`` and full context instead of
                surfacing an opaque DB integrity error.
        """
        if self._device_template_repository is None:
            logger.error(
                f"[DEFAULT_TEMPLATE] DeviceTemplateRepository not wired for env={self._env}"
            )
            raise DeviceCreationError(
                error_code="LOCAL_TEMPLATE_NOT_CONFIGURED",
                message=(
                    "DeviceTemplateRepository is not wired; no Local template "
                    f"available for env={self._env}"
                ),
                context={
                    "env": self._env,
                    "hint": (
                        "Ensure LocalPaasService is constructed with a "
                        "DeviceTemplateRepository (check dependency wiring)."
                    ),
                },
            )

        template_id = self._device_template_repository.get_default_local_template_id()
        if template_id is None:
            logger.error(
                f"[DEFAULT_TEMPLATE] No Local template configured for env={self._env}"
            )
            raise DeviceCreationError(
                error_code="LOCAL_TEMPLATE_NOT_CONFIGURED",
                message=(f"No Local template configured for env={self._env}"),
                context={
                    "env": self._env,
                    "hint": (
                        "Insert a row into baas_device_template with "
                        "type='Local', status='ONLINE', is_deleted=0."
                    ),
                },
            )

        logger.info(f"[DEFAULT_TEMPLATE] Using template_id={template_id}")
        return template_id

    async def dispatch_to_local_connection(
        self,
        machine_id: str,
        command: dict[str, Any],
        request_id: str | None = None,
    ) -> dict[str, Any]:
        """Same-instance dispatch with cross-process UDS fallback.

        Encapsulates the three-layer same-instance routing decision shared
        between ``_route_command`` (same-instance branch) and
        ``adapters.web.routers.internal_router.internal_forward`` (HTTP receiver for
        cross-instance forwards). Centralising the logic prevents the two
        callers from drifting — the bug fixed in this method's introduction
        was that ``internal_forward`` implemented only the first layer
        (``is_connected`` in this process), so cross-instance forwards that
        landed in the "wrong" worker on the target instance returned
        ``MACHINE_NOT_CONNECTED`` instead of being UDS-forwarded to the
        worker that actually owned the WebSocket.

        Decision tree (same as the prior inline implementation in
        ``_route_command:342-473``):

        1. Same process (``connection_manager.is_connected``): dispatch via
           ``send_command_with_request_id`` (when a ``request_id`` is supplied)
           or ``send_command`` (otherwise). Exceptions from this path
           (``TimeoutError``, ``ConnectionError``, ``asyncio.CancelledError``)
           propagate to the caller — by design (the user's decision tagged
           "异常只在外层包装"): the same-instance/in-process path keeps its
           existing exception contract so ``_route_command``'s callers
           (``start_device`` et al.) continue to receive the same exception
           types as before this refactor. Outer callers must wrap to their
           own contract: ``_route_command`` re-raises into
           ``DeviceCreationError``; ``internal_forward`` already catches
           ``TimeoutError``/``ConnectionError`` and returns dicts.
        2. Cross-process (route_info.worker_pid != current PID): forward via
           ``worker_router.forward_to_worker``. The receiving worker's UDS
           server returns either a raw mng success response or an error
           envelope ``{"status": "error", "error": ..., "message": ..., "data": ...}``.
           For error envelopes, sender-side routing diagnostics
           (``target_worker_pid``, ``socket_path``) are merged into
           ``response["data"]`` so ``_route_command`` can populate
           ``DeviceCreationError.context`` without re-querying ``route_info``.
        3. Same-PID race (CR-02): route_info points to this PID but
           ``is_connected`` is False — the previous worker that wrote that
           route_info crashed; clear the stale row so the next heartbeat from
           any worker can claim the machine, then fall through to
           ``MACHINE_NOT_CONNECTED``.
        4. Fall-through (no ``worker_router`` configured, ``RouteNotFoundError``,
           ``WorkerOfflineError``, or generic ``Exception``): return
           ``MACHINE_NOT_CONNECTED`` error dict. ``asyncio.CancelledError``
           is re-raised — task cancellation must never be swallowed.

        Args:
            machine_id: Target machine identifier.
            command: Command dict with "action" and "params" keys. If
                ``"request_id"`` is already set on the command, it is preserved
                (highest priority); otherwise the ``request_id`` argument is
                used; otherwise a fresh ``{machine_id}|{uuid.hex}`` is
                generated. Mutates ``command["request_id"]`` only on the
                cross-process path (the UDS envelope needs the id threaded
                through for end-to-end tracing).
            request_id: Optional pre-generated request id from the caller
                (typically ``internal_forward`` propagating an id minted on
                the source instance). When provided AND the same-process
                path is taken, ``send_command_with_request_id`` is used so the
                id threads through to the mng daemon.

        Returns:
            Raw dict — never raises ``DeviceCreationError``.

            - Success (same-process): whatever ``ConnectionManager.send_command``
              returned (typically the mng payload dict).
            - Success (cross-process): the raw mng response forwarded by the
              receiving worker (already a dict).
            - All routing-layer errors: error envelope dict with keys
              ``status="error"``, ``error`` (code), ``message``, ``data``.
        """
        action = command.get("action", "unknown")

        if self._connection_manager.is_connected(machine_id):
            logger.info(
                f"[ROUTE_SAME] Routing command to same process: "
                f"machine_id={machine_id}, action={action}, "
                f"server_ip={self._server_ip}"
            )
            if request_id is not None:
                return await self._connection_manager.send_command_with_request_id(
                    machine_id, command, request_id
                )
            return await self._connection_manager.send_command(machine_id, command)

        if self._worker_router:
            try:
                route_info = self._worker_router.get_route_for_machine(
                    machine_id, self._env
                )

                if route_info["worker_pid"] != os.getpid():
                    # Cross-process UDS forward. Seed command["request_id"]
                    # so the same id threads: dispatch (sender) →
                    # forward_to_worker envelope → uds_server (receiver) →
                    # send_command_with_request_id → mng → response.
                    # Priority: existing command["request_id"] (preserved
                    # for backward-compat with callers that set their own,
                    # exercised by test_uds_forward_preserves_caller_supplied_request_id)
                    # > caller-supplied request_id argument > generated.
                    # WR-01: delimiter sourced from ConnectionManager constant.
                    command["request_id"] = (
                        command.get("request_id")
                        or request_id
                        or (
                            f"{machine_id}"
                            f"{self._connection_manager.REQUEST_ID_DELIMITER}"
                            f"{uuid.uuid4().hex}"
                        )
                    )
                    logger.info(
                        f"[ROUTE_UDS] Forwarding to worker {route_info['worker_pid']}: "
                        f"machine_id={machine_id}, action={action}, "
                        f"socket={route_info['socket_path']}, "
                        f"request_id={command['request_id']}"
                    )
                    response = await self._worker_router.forward_to_worker(
                        machine_id=machine_id,
                        command=command,
                        route_info=route_info,
                        timeout=None,
                    )

                    # Enrich envelope errors with sender-side routing
                    # diagnostics so _route_command can populate
                    # DeviceCreationError.context without re-fetching
                    # route_info. Mutates a defensive copy only; success
                    # responses pass through verbatim (D-01).
                    if response.get("status") == "error":
                        enriched = dict(response)
                        data = dict(enriched.get("data") or {})
                        data.setdefault("target_worker_pid", route_info["worker_pid"])
                        data.setdefault("socket_path", route_info["socket_path"])
                        enriched["data"] = data
                        return enriched
                    return response

                # CR-02: route_info points to this PID but is_connected is
                # False — previous worker at this PID died before clearing
                # its row. Clear it so other workers can take over after
                # the next heartbeat, then fall through to MACHINE_NOT_CONNECTED.
                logger.debug(
                    f"[ROUTE_RACE] Route info shows current PID but not connected: "
                    f"machine_id={machine_id}, clearing stale route_info"
                )
                self._connection_manager.clear_stale_route_info(machine_id)

            except (WorkerOfflineError, RouteNotFoundError) as e:
                logger.warning(
                    f"[ROUTE_UDS_FAIL] Worker offline or route not found: "
                    f"machine_id={machine_id}, error={e}"
                )
                # Fall through to MACHINE_NOT_CONNECTED
            except asyncio.CancelledError:
                # Task cancellation must never be swallowed.
                raise
            except Exception as e:
                logger.error(f"[ROUTE_UDS_ERROR] Unexpected error: {e}")
                # Fall through to MACHINE_NOT_CONNECTED

        # No local connection in any worker — connection truly lost.
        return {
            "status": "error",
            "error": "MACHINE_NOT_CONNECTED",
            "message": f"Machine {machine_id} WebSocket not connected",
            "data": {
                "machine_id": machine_id,
                "action": action,
                "server_ip": self._server_ip,
                "request_id": request_id,
                "worker_router_available": self._worker_router is not None,
                "hint": "WebSocket disconnected and no cross-process route available",
            },
        }

    async def _route_command(
        self,
        machine_id: str,
        command: dict[str, Any],
        target_instance: str,
    ) -> Any:
        """Route command to same-instance or cross-instance target.

        TOCTOU Protection: Re-queries database at routing time to catch machines
        that went OFFLINE between initial query and actual routing. This prevents:
        1. Same-instance: Commands to disconnected WebSocket (cleared connections)
        2. Cross-instance: HTTP forwarding to wrong/cleared instance

        Args:
            machine_id: The machine ID for the target device.
            command: Command dict with "action" and "params" keys.
            target_instance: Target server instance IP (from initial query, used for logging).

        Returns:
            Command result from ConnectionManager or InstanceRouter.

        Raises:
            DeviceCreationError: If machine went OFFLINE or instance changed.
                Per D-03/D-04, when the UDS-forward path receives an envelope
                error, this method raises ``DeviceCreationError`` whose
                ``error_code`` is the original ``envelope.error`` value
                (e.g., ``WORKER_OFFLINE``, ``CONTAINER_NOT_FOUND``,
                ``MACHINE_NOT_CONNECTED``). The same-instance dispatch is
                delegated to ``dispatch_to_local_connection`` which returns
                raw dicts; this method converts error envelopes back to
                ``DeviceCreationError`` to preserve the existing exception
                contract for upstream callers (``start_device`` et al.).
        """
        action = command.get("action", "unknown")

        # TOCTOU Fix: Re-query database right before routing decision
        # This catches machine transitioning to OFFLINE or changing instances
        record = self._repository.get_by_machine_id(machine_id, self._env)
        if record is None:
            raise DeviceCreationError(
                error_code="MACHINE_NOT_FOUND",
                message=f"Machine {machine_id} not found in database",
                context={
                    "action": action,
                    "hint": "Machine may have been deleted",
                    "original_target_instance": target_instance,
                },
            )

        if record.status == "OFFLINE":
            raise DeviceCreationError(
                error_code="MACHINE_OFFLINE",
                message=f"Machine {machine_id} is OFFLINE",
                context={
                    "machine_id": machine_id,
                    "action": action,
                    "last_connected_instance": record.connected_server_instance,
                    "last_heartbeat": (
                        record.last_heartbeat.isoformat()
                        if record.last_heartbeat
                        else None
                    ),
                    "hint": "Machine disconnected; check mng daemon status",
                },
            )

        current_instance = record.connected_server_instance

        # Defensive: catch empty/cleared target_instance
        if not current_instance:
            raise DeviceCreationError(
                error_code="INSTANCE_NOT_ASSIGNED",
                message=f"Machine {machine_id} has no connected_server_instance assigned",
                context={
                    "machine_id": machine_id,
                    "action": action,
                    "initial_instance": target_instance,  # For debugging mismatch
                    "server_ip": self._server_ip,
                    "hint": "Machine may need re-registration via mng daemon",
                },
            )

        # Log if instance changed between initial query and now
        if target_instance != current_instance:
            logger.warning(
                f"[INSTANCE_CHANGED] Machine {machine_id} instance changed: "
                f"{target_instance} -> {current_instance}, action={action}"
            )

        # Log routing decision for debugging
        logger.info(
            f"[ROUTE_DECISION] machine_id={machine_id}, action={action}, "
            f"db_instance={current_instance}, this_server={self._server_ip}, "
            f"target_instance={target_instance}, current_instance={current_instance}, "
            f"match={current_instance == self._server_ip}"
        )

        if current_instance == self._server_ip:
            # Same-instance: preserve the pre-refactor asymmetry — same-process
            # mng responses pass through verbatim (callers like create_device
            # interpret status=error envelopes as business errors and convert
            # them with their own messages, e.g. "Device creation failed");
            # routing-layer errors (cross-process UDS envelope, race fall-through)
            # propagate as DeviceCreationError.
            if self._connection_manager.is_connected(machine_id):
                logger.info(
                    f"[ROUTE_SAME] Routing command to same process: "
                    f"machine_id={machine_id}, action={action}, "
                    f"server_ip={self._server_ip}"
                )
                return await self._connection_manager.send_command(machine_id, command)

            # Not connected in this process — delegate to the shared
            # dispatcher which encapsulates the cross-process UDS forward,
            # CR-02 race fall-through and final MACHINE_NOT_CONNECTED branch.
            # ``dispatch_to_local_connection`` is also the reuse point for
            # ``internal_router.internal_forward`` (cross-instance receiver
            # that needs the same decision tree). Since this branch is only
            # reached when is_connected was False, any error envelope dispatch
            # returns is by definition a routing-layer error and must
            # propagate as DeviceCreationError to preserve upstream callers'
            # exception surface (asymmetry documented above + verified by
            # TestRouteCommandWorkerRouter cases).
            result = await self.dispatch_to_local_connection(machine_id, command)

            if not isinstance(result, dict) or result.get("status") != "error":
                return result

            error_code = result.get("error") or "MACHINE_NOT_CONNECTED"
            data = result.get("data") or {}

            if error_code == "MACHINE_NOT_CONNECTED":
                # Fall-through path: no live connection in any worker on
                # this instance. Surface the same diagnostic context the
                # inline implementation did before the refactor.
                raise DeviceCreationError(
                    error_code="MACHINE_NOT_CONNECTED",
                    message=f"Machine {machine_id} WebSocket not connected",
                    context={
                        "machine_id": machine_id,
                        "action": action,
                        "server_ip": self._server_ip,
                        "db_instance": current_instance,
                        "target_instance": target_instance,
                        "current_instance": current_instance,
                        "connection_status": "disconnected",
                        "worker_router_available": self._worker_router is not None,
                        "hint": "WebSocket disconnected and no cross-process route available",
                    },
                )

            # UDS envelope error (D-03 / D-05): preserve the original
            # error_code (e.g. WORKER_OFFLINE, CONTAINER_NOT_FOUND) rather
            # than collapsing to MACHINE_NOT_CONNECTED. Sender-side routing
            # diagnostics (target_worker_pid, socket_path) were merged into
            # result["data"] by dispatch_to_local_connection so the existing
            # DeviceCreationError.context shape stays unchanged.
            raise DeviceCreationError(
                error_code=error_code,
                message=result.get("message", "UDS forward failed"),
                context={
                    "machine_id": machine_id,
                    "action": action,
                    "target_worker_pid": data.get("target_worker_pid"),
                    "socket_path": data.get("socket_path"),
                    "original_error": result.get("error"),
                    "envelope_message": result.get("message"),
                },
            )

        # Cross-instance: use InstanceRouter with CURRENT instance
        logger.info(
            f"[ROUTE_CROSS] Routing command to cross-instance: "
            f"machine_id={machine_id}, action={action}, "
            f"target_instance={current_instance}, server_ip={self._server_ip}"
        )
        request_id = (
            f"{machine_id}"
            f"{self._connection_manager.REQUEST_ID_DELIMITER}"
            f"{uuid.uuid4().hex}"
        )
        return await self._instance_router.route_to_instance(
            target_instance=current_instance,
            action=command["action"],
            machine_id=machine_id,
            params=command["params"],
            request_id=request_id,
        )

    async def resolve_ws_conn_info(
        self,
        paas_device_id: str,
        port: int,
        path: str,
        ws_conn_mode: str | None = None,
    ) -> WsConnectionInfo:
        """Resolve WebSocket connection info for a local device.

        Two modes, controlled by the ``ws_conn_mode`` constructor parameter
        (default ``"direct"``, set via ``LOCAL_WS_CONN_MODE`` env var in the
        factory):

        - **Direct**: ``ws://localhost:{port}{path}``, same-machine only.
        - **Relay**: agentclawproxy + open_ws_relay + Plugin, same + cross-machine.

        Args:
            paas_device_id: Bare three-segment local device ID
                (``container_id--machine_id--user_id``).
            port: Target port on the device.
            path: WebSocket path on the device (e.g., ``/api/openclaw/ws``).
            ws_conn_mode: Optional override for the connection mode.
                ``"relay"`` activates agentclawproxy-based relay; any other value
                (including ``None``) falls back to the instance default (``"direct"``).

        Returns:
            WsConnectionInfo with ws_url, token, target, and expires_at.
        """
        mode = ws_conn_mode if ws_conn_mode is not None else self._ws_conn_mode
        if mode == "relay":
            return await self._resolve_ws_conn_info_relay(paas_device_id, port, path)
        return await self._resolve_ws_conn_info_direct(paas_device_id, port, path)

    async def _resolve_ws_conn_info_direct(
        self,
        paas_device_id: str,
        port: int,
        path: str,
    ) -> WsConnectionInfo:
        """Direct localhost WebSocket connection (original implementation).

        Queries mng daemon in real-time via ``get_device_info()`` for the
        device's mapped port, then constructs a direct
        ``ws://localhost:{port}{path}`` URL with empty token and 24-hour expiry.

        Active by default; see ``resolve_ws_conn_info()`` docstring for switching.
        """
        # Step 1: Real-time query mng daemon to get the device's mapped port.
        # Per D-RO04: get_device_info always queries mng daemon (no caching).
        device_info = await self.get_device_info(paas_device_id)

        # Step 2: Type validation — ensure we got the expected LocalDeviceInfo
        if not isinstance(device_info, LocalDeviceInfo):
            raise DeviceCreationError(
                error_code="INVALID_DEVICE_INFO",
                message=(
                    f"Expected LocalDeviceInfo from get_device_info, "
                    f"got {type(device_info).__name__}"
                ),
            )

        # Step 3: Construct direct ws://localhost URL with mng-returned port.
        # The ``port`` parameter is intentionally unused: LOCAL platform gets
        # the actual port from mng daemon, not from the caller.
        actual_port = device_info.port
        if port and port != actual_port:
            logger.info(
                "[PORT_MISMATCH] Caller requested port %d, "
                "mng daemon returned port %d — using daemon value",
                port,
                actual_port,
            )
        target = f"localhost:{actual_port}"
        ws_url = f"ws://localhost:{actual_port}{path}"
        token = ""
        expires_at = datetime.now(UTC) + timedelta(hours=24)

        return WsConnectionInfo(
            ws_url=ws_url,
            token=token,
            target=target,
            expires_at=expires_at,
        )

    async def _resolve_ws_conn_info_relay(
        self,
        paas_device_id: str,
        port: int,
        path: str,
    ) -> WsConnectionInfo:
        """Cross-machine relay WebSocket connection (v1.3 implementation).

        Resolves WebSocket connection info through the agentclawproxy relay
        (agentclawproxy + open_ws_relay + DesktopSandboxPlugin). The same
        architecture supports both same-machine and cross-machine chat —
        same-machine traffic flows through the proxy just like cross-machine,
        making the client-side connection uniform.

        Flow:
            1. Parse three-segment device ID (container_id--machine_id--user_id)
            2. Generate a relay session_id (uuid4 hex)
            3. DB lookup + TOCTOU: verify machine exists and is ONLINE
            4. Pre-create relay session row (insert_init) for lifecycle tracking
            5. Delegate to Plugin for token/target/ws_url/expires_at construction
            6. Send ``open_ws_relay`` command to mng daemon via _route_command
            7. Return WsConnectionInfo from Plugin

        Inactive by default; see ``resolve_ws_conn_info()`` docstring for switching.

        Raises:
            DeviceCreationError:
                - ``MACHINE_NOT_FOUND`` — no database record for the machine.
                - ``MACHINE_OFFLINE`` — machine is not ONLINE.
                - ``RELAY_SETUP_FAILED`` — open_ws_relay command failed.
                - ``RELAY_TIMEOUT`` — mng daemon command timed out.
                - ``RELAY_COMMAND_FAILED`` — mng daemon returned an error.
                - Plugin error codes (via SandboxPluginError → DeviceCreationError).
        """
        # Step 1: Parse three-segment device ID.
        device_id = LocalDeviceId.parse(paas_device_id)
        container_id = device_id.container_id
        machine_id = device_id.machine_id
        user_id = device_id.user_id

        # Step 2: Generate relay session_id.
        session_id = uuid.uuid4().hex

        # Step 3: DB lookup + TOCTOU — verify machine exists and is ONLINE.
        record = self._repository.get_by_machine_id(machine_id, self._env)
        if record is None:
            raise DeviceCreationError(
                error_code="MACHINE_NOT_FOUND",
                message=f"Machine {machine_id} not found in database",
                context={"machine_id": machine_id, "session_id": session_id},
            )
        if record.status != "ONLINE":
            raise DeviceCreationError(
                error_code="MACHINE_OFFLINE"
                if record.status == "OFFLINE"
                else "MACHINE_INVALID",
                message=(
                    f"Machine {machine_id} is OFFLINE"
                    if record.status == "OFFLINE"
                    else f"Machine {machine_id} has unexpected status: {record.status or 'unknown'}"
                ),
                context={
                    "machine_id": machine_id,
                    "session_id": session_id,
                    "last_connected_instance": record.connected_server_instance,
                },
            )

        # Step 4: Pre-create relay session row for lifecycle tracking.
        relay_session_created = False
        if self._relay_repository is not None:
            try:
                self._relay_repository.insert_init(
                    session_id=session_id,
                    machine_id=machine_id,
                    operator=user_id,
                )
            except DeviceCreationError:
                raise
            except Exception as e:
                raise DeviceCreationError(
                    error_code="RELAY_DB_ERROR",
                    message=(
                        f"Failed to create relay session for machine {machine_id}: {e}"
                    ),
                    context={
                        "machine_id": machine_id,
                        "session_id": session_id,
                        "error": str(e),
                    },
                ) from e
            relay_session_created = True

        try:
            # Step 5: Delegate to Plugin for connection parameter construction.
            # The Plugin returns ws_url, token, target, expires_at — no DB ops.
            try:
                conn_info = self._desktop_sandbox_plugin.resolve_ws_conn_info(
                    session_id=session_id,
                    container_id=container_id,
                    machine_id=machine_id,
                    user_id=user_id,
                    port=port,
                    path=path,
                    template_id=self._credentials.template_id,
                )
            except SandboxPluginError as e:
                raise DeviceCreationError(
                    error_code=str(e.error_code),
                    message=str(e),
                    context={
                        "machine_id": machine_id,
                        "session_id": session_id,
                        "plugin_error": str(e),
                    },
                ) from e

            # Step 6: Send open_ws_relay command to mng daemon.
            # The command params carry the Plugin-constructed token and target
            # so mng daemon can set up the relay tunnel.
            command: dict[str, Any] = {
                "action": "open_ws_relay",
                "params": {
                    "session_id": session_id,
                    "token": getattr(conn_info, "token", ""),
                    "target": getattr(conn_info, "target", ""),
                    "port": port,
                },
            }

            try:
                route_result = await self._route_command(
                    machine_id, command, record.connected_server_instance
                )
                # Same-instance path: _route_command returns raw mng
                # response verbatim (including status=error envelopes).
                # Check and convert to DeviceCreationError so callers
                # get a consistent exception surface regardless of the
                # routing path taken.
                if (
                    isinstance(route_result, dict)
                    and route_result.get("status") == "error"
                ):
                    error_code = route_result.get("error") or "RELAY_SETUP_FAILED"
                    raise DeviceCreationError(
                        error_code=error_code,
                        message=route_result.get(
                            "message",
                            f"open_ws_relay failed for {machine_id}",
                        ),
                        context={
                            "machine_id": machine_id,
                            "session_id": session_id,
                            "response": route_result,
                        },
                    )
                logger.info(
                    "[RELAY_OPENED] open_ws_relay response for session %s: %s",
                    session_id,
                    route_result,
                )
            except DeviceCreationError:
                raise
            except TimeoutError as e:
                raise DeviceCreationError(
                    error_code="RELAY_TIMEOUT",
                    message=f"open_ws_relay command timed out for machine {machine_id}",
                    context={
                        "machine_id": machine_id,
                        "session_id": session_id,
                        "error": str(e),
                    },
                ) from e
            except ConnectionError as e:
                raise DeviceCreationError(
                    error_code="RELAY_COMMAND_FAILED",
                    message=f"open_ws_relay connection error for machine {machine_id}: {e}",
                    context={
                        "machine_id": machine_id,
                        "session_id": session_id,
                        "error": str(e),
                    },
                ) from e
            except Exception as e:
                raise DeviceCreationError(
                    error_code="RELAY_SETUP_FAILED",
                    message=f"Failed to open ws relay for machine {machine_id}: {e}",
                    context={
                        "machine_id": machine_id,
                        "session_id": session_id,
                        "error": str(e),
                    },
                ) from e
        except Exception:
            if relay_session_created:
                try:
                    self._relay_repository.update_closed(session_id=session_id)
                except Exception:
                    pass
            raise

        # Step 7: Return Plugin-constructed WsConnectionInfo.
        # Use getattr for compatibility with Plugin returning a duck-typed object
        # (e.g., dataclass or namedtuple) rather than requiring an exact
        # WsConnectionInfo instance.
        ws_url = getattr(conn_info, "ws_url", "")
        if not ws_url:
            raise DeviceCreationError(
                error_code="PLUGIN_ERROR",
                message="Plugin resolved an empty or invalid WebSocket URL",
                context={
                    "machine_id": machine_id,
                    "session_id": session_id,
                },
            )
        token = getattr(conn_info, "token", "")
        target = getattr(conn_info, "target", "")
        expires_at = getattr(
            conn_info,
            "expires_at",
            datetime.now(UTC) + timedelta(hours=24),
        )

        return WsConnectionInfo(
            ws_url=ws_url,
            token=token,
            target=target,
            expires_at=expires_at,
        )

    async def resolve_invoke_http_info(
        self, paas_device_id: str, port: int, path: str | None = None
    ) -> HttpConnectionInfo:
        """Resolve HTTP connection info for a local device via agentclawproxy proxypass.

        Constructs a proxypass HTTP URL pointing to
        agentclawproxy-{env}.example.com and generates a HS256 JWT token for
        the target device. This replaces the previous localhost direct-connection
        approach. The JWT token expires 120 seconds from creation.

        Note: Unlike resolve_ws_conn_info, this method does NOT call
        get_device_info to validate the port — the caller is responsible for
        providing the correct port for the target container. This avoids an
        extra WebSocket round-trip through mng daemon. The JWT token is
        short-lived (120s), limiting the blast radius of a wrong port.

        Args:
            paas_device_id: Bare three-segment local device ID
                (container_id--machine_id--user_id), without @template_id suffix.
                The @template_id is appended internally from credentials.
            port: Target port on the device.
            path: HTTP request path (defaults to "/" when None).

        Returns:
            HttpConnectionInfo with proxypass https URL, HS256 JWT token,
            and target in LOCAL_ format.
        """
        from secbaas.community.core.utils.proxypass_utils import (
            build_proxypass_url,
            generate_proxypass_jwt,
        )

        resolved_path = path if path is not None else "/"
        target = f"LOCAL_{paas_device_id}@{self._credentials.template_id}:{port}"

        http_url = build_proxypass_url(target, resolved_path, scheme="https")
        # Key retrieval via DI container is tracked as known debt —
        # future refactoring should inject the secret plugin into the constructor.
        from secbaas.community.bootstrap import get_container  # noqa: PLC0415

        key = (
            get_container()
            .plugins.secret_plugin()
            .get_secret("other_manual_agentclawproxy_proxypass_secret")
        )
        token = generate_proxypass_jwt(target, key, ttl=120)

        return HttpConnectionInfo(http_url=http_url, token=token, target=target)

    async def create_device(
        self,
        config: DeviceCreateConfig,
    ) -> DeviceCreationResult:
        """Create a local Docker container device via mng daemon.

        Args:
            config: DeviceCreateConfig containing user_id, machine_id,
                tc_bot_id, agent_code, and optional envs.

        Returns:
            DeviceCreationResult with container details.

        Raises:
            DeviceCreationError: If mng daemon returns an error response.
            TimeoutError: If command times out (30s via ConnectionManager).
            ConnectionError: If machine is not connected.
            ValueError: If config is not LocalCreateConfig.
        """
        # Validate config type
        if not isinstance(config, LocalCreateConfig):
            raise ValueError(f"Expected LocalCreateConfig, got {type(config).__name__}")

        # Validate mount_path for security (CR-01)
        self._validate_mount_path(config.mount_path)

        # 1. Extract machine_id from config (D-FF02: runtime params from merged config)
        machine_id = config.machine_id

        # 2. Query repository to get connected_server_instance
        record = self._repository.get_by_machine_id(machine_id, self._env)
        if record is None:
            raise DeviceCreationError(
                error_code="MACHINE_NOT_FOUND",
                message=f"Machine {machine_id} not found in database",
            )

        target_instance = record.connected_server_instance

        # 3. Build command payload per WebSocket format
        command = {
            "action": "create_device",
            "params": {
                "name": config.name,
                "description": config.description,
                "user_id": config.user_id,
                "machine_id": config.machine_id,
                "tc_bot_id": config.tc_bot_id,
                "agent_code": config.agent_code,
                "envs": config.envs or {},
                "storage_dir": config.mount_path,
                "credentials": (
                    config.credentials.model_dump(exclude_none=True, by_alias=True)
                    or None
                )
                if config.credentials
                else None,
                "engine_type": config.engine_type,
            },
        }

        # 4. Route command via helper method (same or cross-instance)
        result = await self._route_command(machine_id, command, target_instance)

        # 5. Handle error response (status == "error")
        if result.get("status") == "error":
            raise DeviceCreationError(
                error_code=result.get("error", "CREATION_FAILED"),
                message=_normalize_message(
                    result.get("message", "Device creation failed")
                ),
            )

        # 6. Return LocalCreationResult on success
        data = result.get("data", {}) or {}
        raw_container_id = data.get("container_id")
        if not raw_container_id:
            raise DeviceCreationError(
                error_code="INVALID_RESPONSE",
                message="Missing container_id in creation response",
            )

        # Construct three-part container_id: container_id--machine_id--user_id
        three_part_container_id = (
            f"{raw_container_id}--{config.machine_id}--{config.user_id}"
        )

        return LocalCreationResult(
            container_id=three_part_container_id,
            platform="local",
            status="RUNNING",
        )

    async def destroy_device(self, paas_device_id: str) -> bool:
        """Destroy a local Docker container device via mng daemon.

        Args:
            paas_device_id: Local device ID (format: container_id--machine_id--user_id).

        Returns:
            True if successful. Returns True even if container not found (idempotent).

        Raises:
            DeviceCreationError: If mng daemon returns an error other than CONTAINER_NOT_FOUND.
            ValueError: If paas_device_id format is invalid.
        """
        # 1. Parse paas_device_id
        logger.info(f"[destroy_device] Parsing paas_device_id: {paas_device_id}")
        device_id = LocalDeviceId.parse(paas_device_id)

        # 2. Query repository for routing
        record = self._repository.get_by_machine_id(device_id.machine_id, self._env)
        if record is None:
            raise DeviceCreationError(
                error_code="MACHINE_NOT_FOUND",
                message=f"Machine {device_id.machine_id} not found in database",
            )

        target_instance = record.connected_server_instance

        # 3. Build command payload
        command = {
            "action": "destroy_device",
            "params": {"container_id": device_id.container_id},
        }

        # 4. Route command via helper method (same or cross-instance)
        result = await self._route_command(
            device_id.machine_id, command, target_instance
        )

        # 5. Handle error response
        if result.get("status") == "error":
            error_code = result.get("error", "DESTROY_FAILED")
            # CONTAINER_NOT_FOUND is idempotent - return True
            if error_code == "CONTAINER_NOT_FOUND":
                return True
            raise DeviceCreationError(
                error_code=error_code,
                message=_normalize_message(
                    result.get("message", "Device destruction failed")
                ),
            )

        return True

    async def execute_command(
        self,
        paas_device_id: str,
        cmd: str,
        env: dict[str, str] | None = None,
        timeout_seconds: int = 30,
    ) -> CommandResult:
        """Execute a command on a local Docker container via mng daemon.

        Args:
            paas_device_id: Local device ID (format: container_id--machine_id--user_id).
            cmd: Command string to execute.
            env: Command execution context (environment variables), optional.
            timeout_seconds: Maximum execution time in seconds (default: 30, max: 30).

        Returns:
            CommandResult with execution details. Non-zero exit_code returns
            normally (not as exception) per D-RO05.

        Raises:
            DeviceCreationError: If mng daemon returns transport/business error.
            ValueError: If paas_device_id format is invalid.
        """
        # 1. Parse paas_device_id
        device_id = LocalDeviceId.parse(paas_device_id)

        # 2. Query repository for routing
        record = self._repository.get_by_machine_id(device_id.machine_id, self._env)
        if record is None:
            raise DeviceCreationError(
                error_code="MACHINE_NOT_FOUND",
                message=f"Machine {device_id.machine_id} not found in database",
            )

        target_instance = record.connected_server_instance

        # 3. Build command payload with timeout clamping (per D-RO02)
        actual_timeout = min(timeout_seconds, 30)
        command = {
            "action": "exec_shell",
            "params": {
                "container_id": device_id.container_id,
                "cmd": cmd,
                "env": env or {},
                "timeout_seconds": actual_timeout,
            },
        }

        # 4. Route command via helper method (same or cross-instance)
        result = await self._route_command(
            device_id.machine_id, command, target_instance
        )

        # 5. Handle transport/business errors
        if result.get("status") == "error":
            raise DeviceCreationError(
                error_code=result.get("error", "COMMAND_FAILED"),
                message=_normalize_message(
                    result.get("message", "Command execution failed")
                ),
            )

        # 6. Map mng response to CommandResult (per D-RO05: non-zero exit is valid)
        data = result.get("data") or {}
        return CommandResult(
            exit_code=data.get("exit_code", -1),
            stdout=data.get("stdout", ""),
            stderr=data.get("stderr", ""),
            execution_time_ms=data.get("execution_time_ms", 0),
            command=cmd,
            env=env or {},
        )

    async def invoke_http_in_device(
        self,
        paas_device_id: str,
        method: str,
        port: int,
        path: str,
        query_string: str | None,
        headers: dict[str, str],
        body: bytes,
    ) -> dict[str, Any]:
        """Invoke HTTP request on a local Docker container via mng daemon.

        Acts as a transparent proxy to internal services running inside containers.
        Forwards HTTP requests via WebSocket to mng daemon using invoke_http action.

        Args:
            paas_device_id: Local device ID (format: container--machine--user).
            method: HTTP method (GET, POST, PUT, DELETE, etc.).
            port: Target port on the container.
            path: Request path (e.g., /api/v1/users).
            query_string: Query string including leading '?' or None/empty.
            headers: HTTP headers dict (hop-by-hop headers should be filtered by caller).
            body: Raw request body bytes.

        Returns:
            Dict with keys: status_code (int), headers (dict), body (base64 str).
            The response body is base64-encoded for WebSocket transport.

        Raises:
            DeviceCreationError: If machine not found (MACHINE_NOT_FOUND) or
                mng daemon returns an error (BAD_GATEWAY for pre-invoke errors).
            ValueError: If paas_device_id format is invalid.
        """
        # 1. Parse paas_device_id
        device_id = LocalDeviceId.parse(paas_device_id)

        # 2. Query repository for routing
        record = self._repository.get_by_machine_id(device_id.machine_id, self._env)
        if record is None:
            raise DeviceCreationError(
                error_code="MACHINE_NOT_FOUND",
                message=f"Machine {device_id.machine_id} not found in database",
            )

        target_instance = record.connected_server_instance

        # 3. Build command payload
        # Normalize query_string: ensure it starts with '?' if not empty/None
        normalized_query = query_string if query_string else ""
        if normalized_query and not normalized_query.startswith("?"):
            normalized_query = "?" + normalized_query

        command = {
            "action": "invoke_http",
            "params": {
                "container_id": device_id.container_id,
                "method": method.upper(),
                "port": port,
                "path": path,
                "query_string": normalized_query,
                "headers": headers,
                "body": base64.b64encode(body).decode("utf-8"),
                "timeout_seconds": 25,
            },
        }

        # 4. Route command via helper method (same or cross-instance)
        result = await self._route_command(
            device_id.machine_id, command, target_instance
        )

        # 5. Handle error response
        if result.get("status") == "error":
            error_code = result.get("error", "BAD_GATEWAY")
            # Map specific mng error codes to local error codes
            if error_code == "CONTAINER_NOT_FOUND":
                mapped_code = "CONTAINER_NOT_FOUND"
            else:
                mapped_code = "BAD_GATEWAY"
            raise DeviceCreationError(
                error_code=mapped_code,
                message=_normalize_message(
                    result.get("message", "HTTP invocation failed")
                ),
            )

        # 6. Return result data
        data = result.get("data", {})
        if not isinstance(data, dict):
            raise DeviceCreationError(
                error_code="BAD_GATEWAY",
                message=f"Invalid response format from mng daemon: expected dict, got {type(data).__name__}",
            )

        # Validate required fields
        if "status_code" not in data:
            raise DeviceCreationError(
                error_code="BAD_GATEWAY",
                message="Missing status_code in mng daemon response",
            )

        return data

    async def get_device_info(self, paas_device_id: str) -> DeviceInfo:
        """Get local device info by device ID from mng daemon (real-time query).

        Always queries mng daemon in real-time (no caching) per D-RO04.

        Args:
            paas_device_id: Local device ID (format: container_id--machine_id--user_id).

        Returns:
            LocalDeviceInfo with container status from mng daemon.

        Raises:
            DeviceCreationError: If mng daemon returns an error.
            ValueError: If paas_device_id format is invalid.
        """
        # 1. Parse paas_device_id
        device_id = LocalDeviceId.parse(paas_device_id)

        # 2. Query repository for routing
        record = self._repository.get_by_machine_id(device_id.machine_id, self._env)
        if record is None:
            raise DeviceCreationError(
                error_code="MACHINE_NOT_FOUND",
                message=f"Machine {device_id.machine_id} not found in database",
            )

        target_instance = record.connected_server_instance

        # 3. Build command per D-RO04 (real-time query to mng)
        command = {
            "action": "get_device_info",
            "params": {"container_id": device_id.container_id},
        }

        # 4. Route command via helper method (same or cross-instance)
        #    Wrap in try-except to catch ConnectionError and provide diagnostic context
        try:
            result = await self._route_command(
                device_id.machine_id, command, target_instance
            )
        except ConnectionError as e:
            self._raise_machine_offline_error(device_id.machine_id, record, e)

        # 5. Handle error response
        if result.get("status") == "error":
            raise DeviceCreationError(
                error_code=result.get("error", "QUERY_FAILED"),
                message=_normalize_message(
                    result.get("message", "Device info query failed")
                ),
            )

        # 6. Build LocalDeviceInfo from response (per D-RO04: no caching)
        data = result.get("data") or {}
        return LocalDeviceInfo(
            container_id=device_id.container_id,
            machine_id=device_id.machine_id,
            user_id=device_id.user_id,
            status=data.get("status", "UNKNOWN"),
            platform="local",
            port=data.get("port", -1),
        )

    async def fetch_start_progress(
        self, paas_device_id: str
    ) -> FetchStartProgressResult:
        """Fetch device start/initialization progress from mng daemon.

        Sends a fetch_start_progress WebSocket command to the mng daemon
        and returns the full data dict as FetchStartProgressResult via
        ``extra="allow"`` passthrough (per Phase 11.1 D-03).

        BaaS only validates that ``progress`` is present in the result;
        its type and value are defined by the mng daemon. All other fields
        are mng-daemon-defined and passed through transparently.

        Args:
            paas_device_id: Raw LOCAL device ID (without @template_id suffix),
                format: container_id--machine_id--user_id.

        Returns:
            FetchStartProgressResult with required ``progress`` field
            plus any additional mng-daemon-defined fields.

        Raises:
            DeviceCreationError: If machine not found, mng daemon returns
                an error, or the response is missing ``progress``.
            ValueError: If paas_device_id format is invalid.
        """
        # 1. Parse paas_device_id
        device_id = LocalDeviceId.parse(paas_device_id)

        # 2. Query repository for routing
        record = self._repository.get_by_machine_id(device_id.machine_id, self._env)
        if record is None:
            raise DeviceCreationError(
                error_code="MACHINE_NOT_FOUND",
                message=f"Machine {device_id.machine_id} not found in database",
            )

        target_instance = record.connected_server_instance

        # 3. Build command per D-06: action="fetch_start_progress"
        command = {
            "action": "fetch_start_progress",
            "params": {
                "container_id": device_id.container_id,
            },
        }

        # 4. Route command via helper method (same or cross-instance)
        try:
            result = await self._route_command(
                device_id.machine_id, command, target_instance
            )
        except ConnectionError as e:
            self._raise_machine_offline_error(device_id.machine_id, record, e)

        # 5. Handle error response
        if result.get("status") == "error":
            raise DeviceCreationError(
                error_code=result.get("error", "PROGRESS_FETCH_FAILED"),
                message=_normalize_message(
                    result.get("message", "Start progress fetch failed")
                ),
            )

        # 6. Return FetchStartProgressResult with full data passthrough
        #    (per Phase 11.1 D-03: FetchStartProgressResult(**data))
        data = result.get("data") or {}
        if "progress" not in data:
            raise DeviceCreationError(
                error_code="PROGRESS_FETCH_FAILED",
                message="mng daemon response missing required 'progress' field",
            )
        return FetchStartProgressResult(**data)

    async def update_outbound_operation_rule(
        self,
        paas_device_id: str,
        outbound_operation_rule: OutBoundOperationRule,
    ) -> bool:
        """Update outbound operation rule for a local device.

        Local platform may support limited outbound rule configuration through
        mng daemon. This stub exists for PaasService ABC compliance.

        Args:
            paas_device_id: Local device ID.
            outbound_operation_rule: New outbound operation rule to apply.

        Returns:
            True if successful.

        Raises:
            NotImplementedError: This is a skeleton stub. Local platform
                outbound rules TBD.
        """
        raise NotImplementedError(
            "LocalPaasService.update_outbound_operation_rule is not yet implemented. "
            "Local platform outbound operation rules are not yet defined."
        )

    async def get_machine_info(self, machine_id: str) -> dict[str, Any]:
        """Get machine resource information from mng daemon.

        Queries mng daemon for machine node resources (CPU, memory, disk).
        This is an independent API - results are NOT persistently stored.

        Args:
            machine_id: The machine identifier to query.

        Returns:
            Dictionary with machine resource info (cpu_cores, memory_gb, disk_gb, etc.).

        Raises:
            DeviceCreationError: If machine not found or query fails.
        """
        # 1. Query repository for routing (no device_id parsing needed per D-RO06)
        record = self._repository.get_by_machine_id(machine_id, self._env)
        if record is None:
            raise DeviceCreationError(
                error_code="MACHINE_NOT_FOUND",
                message=f"Machine {machine_id} not found in database",
            )

        target_instance = record.connected_server_instance

        # 2. Build command per D-RO06
        command = {"action": "get_machine_info", "params": {"machine_id": machine_id}}

        # 3. Route command via helper method (same or cross-instance)
        try:
            result = await self._route_command(machine_id, command, target_instance)
        except ConnectionError as e:
            self._raise_machine_offline_error(machine_id, record, e)

        # 4. Handle error response
        if result.get("status") == "error":
            raise DeviceCreationError(
                error_code=result.get("error") or "QUERY_FAILED",
                message=_normalize_message(
                    result.get("message", "Machine info query failed")
                ),
            )

        # 5. Return raw dict data (not persisted per D-RO06)
        data = result.get("data")
        if data is None:
            return {}
        if not isinstance(data, dict):
            raise DeviceCreationError(
                error_code="INVALID_RESPONSE",
                message=f"Expected dict response from get_machine_info, got {type(data).__name__}",
            )
        return data

    def _validate_relative_dir_path(self, path: str) -> None:
        """Validate path parameter to prevent directory traversal attacks.

        Validates a relative directory path (used for mng daemon queries).
        Rejects absolute paths and paths containing directory traversal components.

        Note: paths starting with ~ (e.g. ~/Desktop) are accepted as relative;
        the mng daemon is responsible for tilde expansion.

        Args:
            path: The path string to validate.

        Raises:
            DeviceCreationError: If path contains ".." components or starts with "/".
        """
        # Split into path components and check for traversal.
        # Component-based check avoids false positives on legitimate names
        # containing ".." as a substring (e.g. "backup..v1").
        parts = path.replace("\\", "/").split("/")
        if ".." in parts:
            raise DeviceCreationError(
                error_code="INVALID_PARAMS",
                message=f"Path cannot contain directory traversal sequences (..): {path}",
            )
        if path.startswith("/"):
            raise DeviceCreationError(
                error_code="INVALID_PARAMS",
                message=f"Path must be relative (cannot start with /): {path}",
            )

    def _validate_mount_path(self, path: str | None) -> None:
        """Validate mount_path for security.

        Allows absolute paths (common for host mounts) but blocks
        directory traversal and obviously dangerous paths.

        Args:
            path: The mount path string to validate, or None.

        Raises:
            DeviceCreationError: If path contains ".." or is a dangerous system path.
        """
        if path is None:
            return

        # Only absolute paths allowed (must start with /)
        if not path.startswith("/"):
            raise DeviceCreationError(
                error_code="INVALID_PARAMS",
                message=f"mount_path must be absolute path (must start with /): {path}",
            )

        # Block directory traversal on raw path BEFORE normalization.
        # os.path.normpath collapses ".." segments (e.g. /home/../tmp → /tmp),
        # so checking after normalization would miss traversal attempts.
        if ".." in path:
            raise DeviceCreationError(
                error_code="INVALID_PARAMS",
                message=f"mount_path cannot contain directory traversal sequences: {path}",
            )

        # Normalize path for subsequent checks
        normalized = os.path.normpath(path)

        # Block system directories with separator-aware prefix matching.
        # Using trailing "/" prevents false positives like /etc2 or /binfoo
        # while still matching /etc/, /etc/foo, etc.
        blocked_prefixes = [
            "/etc/",
            "/bin/",
            "/sbin/",
            "/boot/",
            "/dev/",
            "/proc/",
            "/sys/",
            "/root/",
        ]
        for prefix in blocked_prefixes:
            # Exact match (e.g. /etc) or starts with prefix (e.g. /etc/foo)
            if normalized == prefix[:-1] or normalized.startswith(prefix):
                raise DeviceCreationError(
                    error_code="INVALID_PARAMS",
                    message=f"mount_path cannot mount system directory: {path}",
                )

    @staticmethod
    def _raise_machine_offline_error(
        machine_id: str,
        record,
        error: ConnectionError,
    ) -> None:
        """Raise MACHINE_OFFLINE DeviceCreationError with diagnostic context.

        Wraps a ConnectionError from mng daemon communication failure,
        providing machine record context to help diagnose the issue.

        Args:
            machine_id: The machine identifier that was unreachable.
            record: LocalUserMachineRecord with current status metadata.
            error: The originating ConnectionError.

        Raises:
            DeviceCreationError: Always, with error_code MACHINE_OFFLINE.
        """
        context = {
            "machine_id": machine_id,
            "current_status": record.status,
            "last_connected_instance": record.connected_server_instance,
            "last_heartbeat": (
                record.last_heartbeat.isoformat() if record.last_heartbeat else None
            ),
            "action_hint": "Check if mng daemon is running on the machine",
        }
        raise DeviceCreationError(
            error_code="MACHINE_OFFLINE",
            message=f"Machine {machine_id} is registered but not connected",
            context=context,
        ) from error

    async def get_machine_res_dirs(
        self, machine_id: str, dir: str = "~/Desktop"
    ) -> dict[str, Any]:
        """Get machine resource directory structure from mng daemon.

        Queries mng daemon for directory tree structure on the specified machine.
        Results are NOT persistently stored - fetched fresh from mng each call.

        Args:
            machine_id: The machine identifier to query.
            dir: The directory path to query (default: "~/Desktop").

        Returns:
            Dictionary with directory tree structure:
            {name: string, children?: [...]} where files have only name,
            directories have both name and children.

        Raises:
            DeviceCreationError: If path invalid, machine not found, or query fails.
        """
        # 1. Validate path to prevent directory traversal (per T-19-01)
        self._validate_relative_dir_path(dir)

        # 2. Query repository for routing
        record = self._repository.get_by_machine_id(machine_id, self._env)
        if record is None:
            raise DeviceCreationError(
                error_code="MACHINE_NOT_FOUND",
                message=f"Machine {machine_id} not found in database",
            )

        target_instance = record.connected_server_instance

        # 3. Build command per WebSocket protocol
        command = {
            "action": "get_machine_res_dirs",
            "params": {"machine_id": machine_id, "dir": dir},
        }

        # 4. Route command via helper method
        try:
            result = await self._route_command(machine_id, command, target_instance)
        except ConnectionError as e:
            self._raise_machine_offline_error(machine_id, record, e)

        # 5. Handle error response from mng
        if result.get("status") == "error":
            raise DeviceCreationError(
                error_code=result.get("error") or "QUERY_FAILED",
                message=_normalize_message(
                    result.get("message", "Directory query failed")
                ),
            )

        # 6. Return directory tree data (not persisted)
        data = result.get("data")
        if data is None:
            return {}
        if not isinstance(data, dict):
            raise DeviceCreationError(
                error_code="INVALID_RESPONSE",
                message=f"Expected dict response from get_machine_res_dirs, got {type(data).__name__}",
            )
        return data

    async def update_device_ttl(self, paas_device_id: str) -> None:
        """Extend device TTL - not supported for local platform.

        Local devices use persistent containers without automatic TTL expiration.
        This method exists for PaasService ABC compliance but always raises
        NotImplementedError as local platform does not support TTL extension.

        Args:
            paas_device_id: Local device ID.

        Returns:
            Never returns - always raises NotImplementedError.

        Raises:
            NotImplementedError: Local platform does not support TTL extension.
        """
        raise NotImplementedError(
            "LocalPaasService does not support TTL extension. "
            "Local devices are persistent and do not have automatic TTL expiration."
        )

    async def handle_mng_register(
        self,
        machine_id: str,
        user_id: str,
        machine_name: str | None = None,
    ) -> None:
        """Handle mng process registration.

        Implements D-D01 pattern (SELECT first, then INSERT or UPDATE).
        Sets machine status to ONLINE on registration.

        Args:
            machine_id: Unique machine identifier from mng daemon.
            user_id: User ID from login context.
            machine_name: Optional human-readable machine name.

        Returns:
            None

        Raises:
            ValueError: If machine_id is empty.
            DeviceCreationError: WR-05: when the machine is new and no Local
                template is configured (``LOCAL_TEMPLATE_NOT_CONFIGURED``).
                Registration now fails fast with context instead of silently
                inserting ``template_id=0``.
        """
        if not machine_id:
            raise ValueError("machine_id is required")

        # Build machine_info from machine_name per D-M04
        machine_info: dict[str, str] | None = None
        if machine_name:
            machine_info = {"machine_name": machine_name}

        # Check if machine already exists per D-D01
        existing = self._repository.get_by_machine_id(machine_id, self._env)

        if existing:
            # Machine exists: update machine_info and set status ONLINE
            self._repository.update_machine_info(machine_id, self._env, machine_info)
            self._repository.update_status(machine_id, self._env, "ONLINE")
        else:
            # New machine: insert with status ONLINE
            # Phase 18.5: Query default Local template ID from database
            default_template_id = self._get_default_local_template_id()
            self._repository.insert_machine(
                template_id=default_template_id,
                user_id=user_id,
                machine_id=machine_id,
                machine_info=machine_info,
                last_heartbeat=datetime.now(UTC),
                connected_server_instance=self._server_ip,
                status="ONLINE",
                env=self._env,
            )

    async def handle_mng_heartbeat(
        self,
        machine_id: str,
    ) -> None:
        """Handle mng process heartbeat.

        Updates the last_heartbeat timestamp for the machine.

        Args:
            machine_id: Unique machine identifier from mng daemon.

        Returns:
            None

        Raises:
            ValueError: If machine_id is empty.
        """
        if not machine_id:
            raise ValueError("machine_id is required")

        self._repository.update_heartbeat(
            machine_id=machine_id,
            env=self._env,
            timestamp=datetime.now(UTC),
        )

    async def handle_mng_disconnect(
        self,
        machine_id: str,
    ) -> None:
        """Handle mng process disconnect.

        Sets machine status to OFFLINE per D-D02.
        Also updates all ACTIVE devices on this machine to OFFLINE (Phase 33).

        Args:
            machine_id: Unique machine identifier from mng daemon.

        Returns:
            None

        Raises:
            ValueError: If machine_id is empty.
        """
        if not machine_id:
            raise ValueError("machine_id is required")

        # Step 1: Update machine status to OFFLINE (existing logic)
        self._repository.update_status(
            machine_id=machine_id,
            env=self._env,
            status="OFFLINE",
        )

        # Step 2: Update all ACTIVE devices on this machine to OFFLINE (Phase 33)
        # 2.1: Query machine record to get user_id
        machine = self._repository.get_by_machine_id(machine_id, self._env)
        if machine is None:
            logger.warning(
                f"[MNG_DISCONNECT] Machine {machine_id} not found, cannot update device statuses"
            )
            return

        # 2.2: Check device repository availability (defensive)
        if self._device_repository is None:
            logger.warning(
                f"[MNG_DISCONNECT] DeviceRepository not available, cannot update device statuses for machine {machine_id}"
            )
            return

        # 2.3: Query ACTIVE devices for this machine+user
        devices = self._device_repository.list_active_local_devices_by_machine_user(
            machine_id=machine_id,
            user_id=machine.user_id,
            env=self._env,
        )

        # 2.4: If no ACTIVE devices, return silently (D-04: silent for empty results)
        if not devices:
            return

        # 2.5: Batch update devices to OFFLINE
        device_ids = [d.id for d in devices]
        updated_count = self._device_repository.batch_update_status_to_offline(
            device_ids=device_ids,
            env=self._env,
        )

        # 2.6: Log the result
        logger.info(
            f"[MNG_DISCONNECT_DEVICE_UPDATE] machine={machine_id}, found={len(devices)}, updated={updated_count}"
        )

    async def list_machines_by_user(self, user_id: str) -> list[LocalUserMachineRecord]:
        """List ONLINE machines for a specific user in the current environment.

        Queries the repository for machine records associated with the given
        user_id, filtered by the current environment (self._env) and status=ONLINE.

        Args:
            user_id: The user ID to query machines for.

        Returns:
            List of LocalUserMachineRecord objects with status=ONLINE for the user.
            Returns an empty list (not None) if the user has no online machines.

        Raises:
            ValueError: If user_id is empty or None.

        Note:
            This method does not filter internal fields - that responsibility
            lies with the router/API layer that converts records to response models.
        """
        if not user_id:
            raise ValueError("user_id is required")

        records = self._repository.list_by_user_id(user_id, self._env)
        # Filter to only ONLINE machines for API response
        return [r for r in records if r.status == "ONLINE"]

    async def restart_device(self, paas_device_id: str) -> bool:
        """Restart a local Docker container device via mng daemon.

        Args:
            paas_device_id: Local device ID (format: container_id--machine_id--user_id).

        Returns:
            True if successful.

        Raises:
            DeviceCreationError: If mng daemon returns an error.
            ValueError: If paas_device_id format is invalid.
        """
        # 1. Parse paas_device_id
        logger.info(f"[restart_device] Parsing paas_device_id: {paas_device_id}")
        device_id = LocalDeviceId.parse(paas_device_id)

        # 2. Query repository for routing
        record = self._repository.get_by_machine_id(device_id.machine_id, self._env)
        if record is None:
            raise DeviceCreationError(
                error_code="MACHINE_NOT_FOUND",
                message=f"Machine {device_id.machine_id} not found in database",
            )

        target_instance = record.connected_server_instance

        # 3. Build command payload
        command = {
            "action": "restart_device",
            "params": {"container_id": device_id.container_id},
        }

        # 4. Route command via helper method (same or cross-instance)
        result = await self._route_command(
            device_id.machine_id, command, target_instance
        )

        # 5. Handle error response
        if result.get("status") == "error":
            raise DeviceCreationError(
                error_code=result.get("error", "RESTART_FAILED"),
                message=_normalize_message(
                    result.get("message", "Device restart failed")
                ),
            )

        return True

    async def update_device(
        self,
        paas_device_id: str,
        config: DeviceCreateConfig | None = None,
    ) -> bool:
        """Update a local Docker container device configuration.

        TODO: 当前暂用 restart API 代替，待 mng daemon 支持原生
        update_device 语义（传递 envs/mount_path 等配置变更）后替换。

        Args:
            paas_device_id: Local device ID (format: container_id--machine_id--user_id).
            config: Platform-specific device create configuration for the update.
                If None, defaults to restart-only behavior.
                Defaults to None for backward compatibility.

        Returns:
            True if successful.
        """
        return await self.restart_device(paas_device_id)

    async def open_folder(
        self, paas_device_id: str, folder_path: str | None = None
    ) -> bool:
        """Open a folder in the container's file explorer via mng daemon.

        Args:
            paas_device_id: Local device ID (format: container_id--machine_id--user_id).
            folder_path: Optional folder path to open. When None, mng uses its
                default folder. The key is excluded from the command params
                when None.

        Returns:
            True if successful.

        Raises:
            DeviceCreationError: If mng daemon returns an error.
            ValueError: If paas_device_id format is invalid.
        """
        # 1. Parse paas_device_id
        logger.info(f"[open_folder] Parsing paas_device_id: {paas_device_id}")
        device_id = LocalDeviceId.parse(paas_device_id)

        # 2. Query repository for routing
        record = self._repository.get_by_machine_id(device_id.machine_id, self._env)
        if record is None:
            raise DeviceCreationError(
                error_code="MACHINE_NOT_FOUND",
                message=f"Machine {device_id.machine_id} not found in database",
            )

        target_instance = record.connected_server_instance

        # 3. Build command payload (conditional folder_path per D-03)
        params: dict[str, str] = {"container_id": device_id.container_id}
        if folder_path is not None:
            params["folder_path"] = folder_path
        command = {"action": "open_folder", "params": params}

        # 4. Route command via helper method
        result = await self._route_command(
            device_id.machine_id, command, target_instance
        )

        # 5. Handle error response
        if result.get("status") == "error":
            raise DeviceCreationError(
                error_code=result.get("error", "OPEN_FOLDER_FAILED"),
                message=_normalize_message(result.get("message", "Open folder failed")),
            )

        return True

    async def handle_callback(
        self,
        machine_id: str,
        action: str,
        params: dict,
    ) -> dict | None:
        """Handle callback from mng daemon.

        Routes callbacks to specific handlers based on action type.
        Fire-and-forget callbacks return None.
        Request-response callbacks may return a dict with status/data.

        Per Decision 2 (IF-ELIF CHAIN): Simple routing without indirection
        until more callbacks warrant a framework.

        Args:
            machine_id: The machine identifier sending the callback.
            action: The callback action type (e.g., "container_ready").
            params: Action-specific parameters from the callback payload.

        Returns:
            None for fire-and-forget callbacks.
            Dict with {"status": "ok", "data": {...}} for response callbacks.
            None if action is unknown.
        """
        if action == "container_ready":
            return await self._handle_container_ready(machine_id, params)
        # FUTURE: Add elif branches here for new callbacks per Decision 5
        # Example: elif action == "container_exit":
        #     return await self._handle_container_exit(machine_id, params)
        else:
            logger.warning(f"[CALLBACK_UNKNOWN] action={action} machine={machine_id}")
            return None

    async def _destroy_orphan_with_logging(
        self, paas_device_id: str, triple_id: str
    ) -> None:
        """Destroy orphan container with exception logging.

        Per WR-01 fix: Wraps destroy_device in try/except to capture and log
        exceptions that would otherwise be lost in fire-and-forget tasks.
        Per Layer Responsibility: local_paas_service works with bare triple-ID
        (container--machine--user), @template_id suffix is facade layer's duty.

        Args:
            paas_device_id: Bare triple-ID (container_id--machine_id--user_id).
            triple_id: Triple-ID for logging (same as paas_device_id).
        """
        try:
            await self.destroy_device(paas_device_id)
            logger.info(
                f"[HEARTBEAT_ORPHAN_DELETED] Successfully deleted orphan: {triple_id}"
            )
        except Exception as e:
            logger.error(
                f"[HEARTBEAT_ORPHAN_DELETE_FAILED] Failed to delete orphan {triple_id}: "
                f"{type(e).__name__}: {e}",
                exc_info=True,
            )

    async def _process_publish_callback_for_device(
        self, device: DeviceRecord, source: str
    ) -> None:
        """Process publish callback for a device with PROCESSING status record.

        Checks if there's an unpublished (PROCESSING status) publish record for the
        device, and if so, sends a success callback to complete the publish flow.

        Idempotent; safe to call from heartbeat or container_ready paths.
        Never raises — all exceptions are swallowed and logged with exc_info=True
        (Phase 34 D-05: self-wrapped try/except pattern, mirrors
        _destroy_orphan_with_logging).

        Idempotency note (Phase 34 WR-01): the repository's
        ``get_latest_processing_record_by_device`` SQL hardcodes
        ``WHERE result_status = 'PROCESSING'``, so any record returned here is
        already PROCESSING by contract. The real cross-trigger idempotency
        guard lives one layer down in
        ``DefaultPublishService.handle_device_callback`` via
        ``update_result_if_processing`` (optimistic lock); concurrent
        heartbeat + container_ready races resolve there with the loser
        taking the "concurrent callback ignored" path.

        Per Phase 34 D-06: signature is locked as (self, device, source) -> None.
        Per Phase 34 D-13: stdout text is selected by source — "heartbeat" picks the
        heartbeat-specific copy, anything else (including "container_ready") uses
        the original container_ready copy.
        Per Phase 34 D-14: source is NOT whitelist-validated; trust internal caller.
        Per Phase 34 D-15: tenant is derived solely from device.tenant (no tenant
        parameter accepted).
        Per Phase 34 D-16: when self._publish_record_repository is None, log
        [PUBLISH_CALLBACK_ERROR] and return without touching repository or service.

        Args:
            device: The device record to process. Must expose id, device_uuid,
                tenant attributes.
            source: Trigger source identifier ("heartbeat" / "container_ready" /
                future extensions). Used for stdout text selection and to tag
                every log line with the trigger origin.

        Returns:
            None (fire-and-forget; never raises exceptions).
        """
        try:
            if self._publish_record_repository is None:
                logger.warning(
                    "[PUBLISH_CALLBACK_ERROR] PublishRecordRepository not configured, "
                    f"skipping for device {device.device_uuid} (source={source})"
                )
                return

            record = (
                self._publish_record_repository.get_latest_processing_record_by_device(
                    device_id=device.id, tenant=device.tenant, env=self._env
                )
            )
            if not record:
                logger.info(
                    f"[PUBLISH_CALLBACK_SKIP] No PROCESSING record for device="
                    f"{device.device_uuid} (source={source})"
                )
                return

            # Phase 34 WR-01: No DUPLICATE branch here — the repository's SQL
            # already filters result_status='PROCESSING', so any record reaching
            # this point has result_status == "PROCESSING" by contract. Concurrent
            # double-fire (heartbeat + container_ready) is resolved one layer
            # down in DefaultPublishService.handle_device_callback via the
            # update_result_if_processing optimistic lock.

            # Deferred imports: avoids circular dependency between
            # secbaas.core.service.paas and secbaas.core.service.publish_manage
            # (which transitively imports paas._factory
            # -> paas._local_paas_service). Pyright/mypy still
            # type-check these symbols via the inline imports at this scope.
            # Move to module top once the cycle is resolved.
            from secbaas.community.api.publish_manage import DeviceCallbackRequest

            stdout_text = (
                "Local platform: heartbeat container_ready processed"
                if source == "heartbeat"
                else "Local platform: container_ready callback processed"
            )

            callback = DeviceCallbackRequest(
                device_uuid=device.device_uuid,
                publish_id=record.publish_id,
                event_type="start",
                result_status="SUCCESS",
                exit_code=0,
                stdout=stdout_text,
                stderr="",
                tenant=device.tenant,
            )
            from secbaas.community.bootstrap import get_container  # noqa: PLC0415

            result = (
                await get_container()
                .services.publish_service()
                .handle_device_callback(callback)
            )
            logger.info(
                f"[PUBLISH_CALLBACK_SUCCESS] device={device.device_uuid}, "
                f"publish_id={record.publish_id}, source={source}, result={result}"
            )
        except Exception as e:
            # Phase 34 WR-03: Promote to logger.error to mirror
            # _destroy_orphan_with_logging template (D-05). Real failures
            # (DB outage, RPC timeout, unexpected exceptions) must surface
            # at ERROR so ops dashboards filtering on level=error catch them.
            logger.error(
                f"[PUBLISH_CALLBACK_ERROR] device="
                f"{getattr(device, 'device_uuid', 'unknown')}, source={source}, "
                f"error_type={type(e).__name__}, error={e}",
                exc_info=True,
            )

    async def handle_heartbeat_containers(
        self, machine_id: str, user_id: str, bot_list: list[dict]
    ) -> None:
        """Process heartbeat container status updates and orphan deletion.

        Parses the optional payload.bot_list from MNG heartbeat messages and updates
        device status reactively without waiting for callback messages. Handles orphan
        containers (containers without corresponding device records) by triggering
        automatic deletion.

        Per Decision D-01: Business logic in service layer, not WebSocket layer.
        Per Decision D-02: Triple-ID prefix query without explicit tenant.
        Per Decision D-03: Log-and-continue error handling.
        Per Decision D-04: Fire-and-forget orphan deletion via asyncio.create_task.
        Per Decision D-05: State mapping ok->ACTIVE, other->FAILED.

        Args:
            machine_id: The machine identifier from connection context.
            user_id: The user identifier from connection metadata (JWT).
            bot_list: List of container status dicts with "container_id" and "status" keys.

        Returns:
            None (fire-and-forget, never raises exceptions).
        """
        if not self._device_repository:
            logger.warning(
                "[HEARTBEAT_CONTAINER_ERROR] DeviceRepository not configured, "
                f"skipping container processing for machine {machine_id}"
            )
            return

        for item in bot_list:
            try:
                container_id = item.get("container_id")
                status = item.get("status")

                if not container_id:
                    logger.warning(
                        "[HEARTBEAT_CONTAINER_ERROR] Missing container_id in bot_list item: "
                        f"{item}"
                    )
                    continue

                # Construct triple-ID for prefix query
                triple_id = f"{container_id}--{machine_id}--{user_id}"

                # Look up device by triple-ID prefix
                device = self._device_repository.get_by_provider_device_id_prefix(
                    prefix=triple_id, env=self._env
                )

                if device is None:
                    # Orphan container - no matching device record
                    logger.warning(
                        f"[HEARTBEAT_ORPHAN_DELETE] Orphan container detected: {triple_id}"
                    )
                    # Fire-and-forget orphan deletion using bare triple-ID
                    # Per layer responsibility: @template_id suffix is facade's duty
                    asyncio.create_task(
                        self._destroy_orphan_with_logging(triple_id, triple_id)
                    )
                    continue

                # If device status is RELEASED, treat as orphan (business deleted but physical remains)
                if device.status == DeviceStatus.RELEASED.value:
                    logger.warning(
                        f"[HEARTBEAT_RELEASED_DELETE] Container for RELEASED device detected: "
                        f"triple_id={triple_id}, device_uuid={device.device_uuid}, "
                        f"container_status={status}"
                    )
                    asyncio.create_task(
                        self._destroy_orphan_with_logging(triple_id, triple_id)
                    )
                    continue

                # Determine new status based on container status
                new_status = (
                    DeviceStatus.ACTIVE if status == "ok" else DeviceStatus.OFFLINE
                )

                # Update device status if changed
                if device.status != new_status.value:
                    self._device_repository.update_status(
                        device_id=device.id,
                        tenant=device.tenant,
                        env=self._env,
                        status=new_status.value,
                    )
                    logger.info(
                        f"[HEARTBEAT_DEVICE_UPDATE] Updated device {device.device_uuid} "
                        f"status from {device.status} to {new_status.value}"
                    )

                    # Phase 34 D-01/D-03/D-04/D-08: On OFFLINE->ACTIVE (or any
                    # non-ACTIVE->ACTIVE) transition, fire-and-forget publish
                    # callback to cover the case where container_ready callback
                    # was lost. Skip when repository is unconfigured.
                    if (
                        new_status == DeviceStatus.ACTIVE
                        and self._publish_record_repository is not None
                    ):
                        asyncio.create_task(
                            self._process_publish_callback_for_device(
                                device, source="heartbeat"
                            )
                        )
                        logger.info(
                            f"[HEARTBEAT_PUBLISH_TRIGGERED] device={device.device_uuid}, "
                            f"container={container_id}"
                        )

                # Log container status
                if status == "ok":
                    logger.debug(
                        f"[HEARTBEAT_CONTAINER_OK] Container {container_id} healthy, "
                        f"device {device.device_uuid} ACTIVE"
                    )
                else:
                    logger.warning(
                        f"[HEARTBEAT_CONTAINER_ERROR] Container {container_id} "
                        f"status={status}, device {device.device_uuid} OFFLINE"
                    )

            except Exception as e:
                logger.warning(
                    f"[HEARTBEAT_CONTAINER_ERROR] Failed to process container: "
                    f"container_id={item.get('container_id', 'unknown')}, "
                    f"error_type={type(e).__name__}, error={e}",
                    exc_info=True,
                )
                # Continue to next container (log-and-continue pattern)
                continue

    async def _handle_container_ready(self, machine_id: str, params: dict) -> None:
        """Handle container_ready callback: query device and process callback.

        Per D-09 to D-16: Full callback processing with idempotency and error handling.
        Replaces Phase 21 auto-callback simulation with native MNG callback handling.

        Args:
            machine_id: The machine identifier where container started.
            params: Callback params containing container_id and tenant.

        Returns:
            None (fire-and-forget callback)
        """
        container_id = params.get("container_id")
        if not container_id:
            raise DeviceCreationError(
                error_code="MISSING_CONTAINER_ID",
                message="container_ready callback missing required container_id",
            )
        env = self._env

        logger.info(
            f"[CALLBACK_CONTAINER_READY] machine={machine_id}, container={container_id}"
        )

        # Check if repositories are available
        if self._device_repository is None or self._publish_record_repository is None:
            logger.warning(
                f"[CALLBACK_CONTAINER_READY] Repositories not configured - "
                f"device_repository={self._device_repository is not None}, "
                f"publish_record_repository={self._publish_record_repository is not None}"
            )
            return

        # Step 1: Query baas_local_user_machine for user_id
        machine_record = self._repository.get_by_machine_id(machine_id, env)
        if not machine_record:
            logger.warning(
                f"[CALLBACK_CONTAINER_READY] Machine not found: {machine_id}"
            )
            return

        user_id = machine_record.user_id
        prefix = f"{container_id}--{machine_id}--{user_id}"

        # Step 2: Query device by prefix
        device = self._device_repository.get_by_provider_device_id_prefix(prefix, env)
        if not device:
            logger.error(
                f"[CALLBACK_CONTAINER_READY] Device not found for prefix: {prefix}, "
                f"container_id={container_id}, machine_id={machine_id}"
            )
            return

        # tenant is derived inside _process_publish_callback_for_device from
        # device.tenant (D-15) — no local binding needed here.

        # Phase 34 D-09/D-10/D-11/D-12: Delegate publish callback completion to
        # the shared _process_publish_callback_for_device worker. Single source
        # of truth for [PUBLISH_CALLBACK_*] log family, idempotency check,
        # DeviceCallbackRequest construction, and exception swallowing.
        logger.info(
            f"[CALLBACK_CONTAINER_READY_TRIGGERED] device={device.device_uuid}, "
            f"container={container_id}"
        )
        await self._process_publish_callback_for_device(
            device, source="container_ready"
        )

    async def pull_file_from_url(
        self,
        paas_device_id: str,
        source_url: str,
        device_path: str,
        timeout_seconds: int = 300,
    ) -> None:
        """Not supported: Local platform does not support file transfer.

        Args:
            paas_device_id: Local device ID (container_id--machine_id--user_id).
            source_url: URL to download from.
            device_path: Destination path on device.
            timeout_seconds: Maximum download time (unused).

        Raises:
            NotImplementedError: Always — file transfer not supported on Local.
        """
        raise NotImplementedError("File transfer not supported on Local platform")

    async def push_file_to_url(
        self,
        paas_device_id: str,
        device_path: str,
        target_url: str,
        timeout_seconds: int = 300,
    ) -> None:
        """Not supported: Local platform does not support file transfer.

        Args:
            paas_device_id: Local device ID (container_id--machine_id--user_id).
            device_path: Source path on device.
            target_url: URL to upload to.
            timeout_seconds: Maximum upload time (unused).

        Raises:
            NotImplementedError: Always — file transfer not supported on Local.
        """
        raise NotImplementedError("File transfer not supported on Local platform")
