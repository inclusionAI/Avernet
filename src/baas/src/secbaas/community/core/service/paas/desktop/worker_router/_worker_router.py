"""WorkerRouter - Main entry for cross-process mng command routing.

Orchestrates UDS server, route info queries, and worker selection.
Per D-20: Independent module for process-level routing.
"""

import asyncio
import json
import os
from typing import TYPE_CHECKING, Any

from secbaas.community.logger import get_logger

from ._exceptions import (
    RouteNotFoundError,
    WorkerOfflineError,
    WorkerRouterError,
)
from ._models import (
    UDSConfig,
    WorkerRouteInfo,
)
from ._uds_server import UDSServer

if TYPE_CHECKING:
    from secbaas.community.core.repository.local_user_machine import (
        LocalUserMachineRepository,
    )

    from .._connection_manager import ConnectionManager

logger = get_logger("core-service")


class WorkerRouter:
    """Main router for cross-process mng connection forwarding.

    Responsibilities:
    1. Manage UDS server lifecycle (start/stop)
    2. Query route_info from database
    3. Determine if machine connection is local vs remote

    Per D-20: Extracted as independent component.
    Per D-24: Asyncio single-thread model.
    """

    def __init__(
        self,
        repository: "LocalUserMachineRepository",
        connection_manager: "ConnectionManager | None" = None,
        config: UDSConfig | None = None,
    ) -> None:
        """Initialize WorkerRouter.

        Args:
            repository: For route_info database queries.
            connection_manager: For local connection dispatch (Phase 32).
            config: UDS server configuration. Uses defaults if None.
        """
        self._repository = repository
        self._connection_manager = connection_manager
        self._config = config or UDSConfig()
        self._uds_server = UDSServer(
            config=self._config,
            connection_manager=connection_manager,
        )
        self._worker_pid = os.getpid()

    @property
    def socket_path(self) -> str | None:
        """Get current UDS socket path."""
        return self._uds_server.socket_path

    @property
    def worker_pid(self) -> int:
        """Get current worker PID."""
        return self._worker_pid

    async def start(self) -> None:
        """Start the WorkerRouter.

        Starts UDS server for receiving forwarded commands.
        After start, the socket path is available via the ``socket_path`` property.
        """
        path = await self._uds_server.start()
        logger.info(
            f"[WORKER_ROUTER] Started: worker_pid={self._worker_pid}, "
            f"socket_path={path}"
        )

    async def stop(self) -> None:
        """Stop the WorkerRouter.

        Stops UDS server and cleans up socket file.
        """
        await self._uds_server.stop()
        logger.info(f"[WORKER_ROUTER] Stopped: worker_pid={self._worker_pid}")

    def get_route_for_machine(self, machine_id: str, env: str) -> WorkerRouteInfo:
        """Get routing information for a machine.

        Per D-07: Returns worker_pid and socket_path from connected_route_info.

        Args:
            machine_id: Target machine identifier.
            env: Environment (dev, pre, prod).

        Returns:
            WorkerRouteInfo dict with worker_pid and socket_path.

        Raises:
            RouteNotFoundError: If route_info is NULL or machine not found.
            WorkerRouterError: For other repository errors.
        """
        try:
            route_info = self._repository.get_route_info(machine_id, env)
        except Exception as e:
            logger.error(
                f"[WORKER_ROUTER] Repository error getting route for {machine_id}: {e}"
            )
            raise WorkerRouterError(f"Failed to get route info for {machine_id}") from e

        if not route_info:
            logger.debug(f"[WORKER_ROUTER] Route not found for machine {machine_id}")
            raise RouteNotFoundError(machine_id)

        # Validate route_info has required fields
        if "worker_pid" not in route_info or "socket_path" not in route_info:
            logger.error(
                f"[WORKER_ROUTER] Invalid route_info for {machine_id}: {route_info}"
            )
            raise WorkerRouterError(
                f"Invalid route_info format for machine {machine_id}"
            )

        logger.debug(
            f"[WORKER_ROUTER] Route found for {machine_id}: "
            f"worker_pid={route_info['worker_pid']}"
        )

        return WorkerRouteInfo(
            worker_pid=route_info["worker_pid"],
            socket_path=route_info["socket_path"],
        )

    def should_handle_locally(self, machine_id: str) -> bool:
        """Check if machine connection is handled by this worker process.

        Uses ConnectionManager.is_connected() to check if WebSocket
        connection exists in this process.

        Args:
            machine_id: Target machine identifier.

        Returns:
            True if machine connected in this worker process.
            False if connection is in another worker (needs UDS forward).
        """
        if self._connection_manager is None:
            # No connection manager = can't handle anything locally
            return False

        return self._connection_manager.is_connected(machine_id)

    async def forward_to_worker(
        self,
        machine_id: str,
        command: dict[str, Any],
        route_info: WorkerRouteInfo,
        timeout: float | None = None,  # noqa: ASYNC109
    ) -> dict[str, Any]:
        """Forward command to target worker via UDS.

        Per D-06: Frame format [4-byte big-endian length][UTF-8 JSON]
        Per D-07: Command envelope with target_worker_pid verification
        Per D-11: 2-5s connect timeout, then use caller's timeout

        Args:
            machine_id: Target machine identifier.
            command: Command dict with "action" and "params" keys.
            route_info: WorkerRouteInfo with socket_path and worker_pid.
            timeout: Overall timeout for the command (UDS connect uses fixed 5s).

        Returns:
            Response dict from target worker (parsed JSON).

        Raises:
            WorkerOfflineError: If UDS connection fails (maps to WORKER_OFFLINE).
            WorkerRouterError: For other routing failures, including self-forward
                detection when route_info points to the current worker PID.
        """
        import asyncio
        import struct

        socket_path = route_info["socket_path"]
        target_pid = route_info["worker_pid"]

        # D-11 self-forward defence: never UDS-forward to our own socket.
        # PID-reuse edge case (old worker crashed, OS reassigned its PID to us) or
        # caller-side mis-route (forgot to filter target_pid != os.getpid()) must
        # not result in a self-connect that would deadlock the asyncio loop.
        # Caller's generic `except Exception` will fall through to MACHINE_NOT_CONNECTED.
        if target_pid == self._worker_pid:
            logger.warning(
                f"[UDS_FWD_SELF] Self-forward blocked: target_pid={target_pid} "
                f"== current_pid={self._worker_pid}, machine_id={machine_id}, "
                f"socket={socket_path}"
            )
            raise WorkerRouterError(
                f"self-forward detected: target_pid={target_pid} "
                f"== current_pid={self._worker_pid}"
            )

        logger.info(
            f"[UDS_FWD] Forwarding to worker: machine_id={machine_id}, "
            f"target_pid={target_pid}, socket={socket_path}"
        )

        # Build command envelope per D-07
        envelope = {
            "target_worker_pid": target_pid,  # Receiver verifies this matches its PID
            "envelope": {
                "action": command.get("action"),
                "machine_id": machine_id,
                "params": command.get("params", {}),
                "request_id": command.get(
                    "request_id"
                ),  # Preserve for end-to-end tracing
            },
        }

        # Serialize to JSON bytes
        json_bytes = json.dumps(envelope).encode("utf-8")
        frame = struct.pack(">I", len(json_bytes)) + json_bytes

        # UDS connection with fixed short timeout (D-11: 5s for connect)
        connect_timeout = min(timeout, 5.0) if timeout else 5.0

        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_unix_connection(socket_path),
                timeout=connect_timeout,
            )
        except (ConnectionRefusedError, FileNotFoundError) as e:
            logger.error(
                f"[UDS_FWD] Connection refused to worker {target_pid} "
                f"for machine {machine_id}: {e}"
            )
            raise WorkerOfflineError(
                machine_id=machine_id,
                socket_path=socket_path,
                reason="connection_refused",
                original_error=e,
            ) from e
        except TimeoutError as e:
            logger.error(
                f"[UDS_FWD] Connect timeout to worker {target_pid} "
                f"for machine {machine_id}"
            )
            raise WorkerOfflineError(
                machine_id=machine_id,
                socket_path=socket_path,
                reason="connect_timeout",
                original_error=e,
            ) from e
        except OSError as e:
            logger.error(f"[UDS_FWD] OS error connecting to worker {target_pid}: {e}")
            raise WorkerOfflineError(
                machine_id=machine_id,
                socket_path=socket_path,
                reason=f"os_error: {e}",
                original_error=e,
            ) from e

        try:
            # Send framed command
            writer.write(frame)
            await writer.drain()

            # Read response with timeout (remaining time from caller's timeout)
            if timeout is not None:
                response_bytes = await asyncio.wait_for(
                    self._read_framed_response(reader, machine_id, socket_path),
                    timeout=timeout,  # Use caller's full timeout for response
                )
            else:
                response_bytes = await self._read_framed_response(
                    reader, machine_id, socket_path
                )

            # Parse response JSON
            response = json.loads(response_bytes.decode("utf-8"))

            logger.info(
                f"[UDS_FWD] Response from worker {target_pid}: "
                f"status={response.get('status')}"
            )

            return response

        except TimeoutError as e:
            logger.error(f"[UDS_FWD] Response timeout from worker {target_pid}")
            raise WorkerOfflineError(
                machine_id=machine_id,
                socket_path=socket_path,
                reason="response_timeout",
                original_error=e,
            ) from e
        except json.JSONDecodeError as e:
            logger.error(
                f"[UDS_FWD] Invalid JSON response from worker {target_pid}: {e}"
            )
            raise WorkerRouterError(
                f"Invalid response from worker {target_pid}: {e}"
            ) from e
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

    async def _read_framed_response(
        self, reader, machine_id: str, socket_path: str
    ) -> bytes:
        """Read framed response from UDS connection.

        Per D-06: Frame format [4-byte big-endian length][N bytes: UTF-8 JSON]

        Args:
            reader: StreamReader from asyncio.open_unix_connection.
            machine_id: The target machine identifier for error context.
            socket_path: The UDS socket path for error context.

        Returns:
            Raw JSON bytes (without length prefix).

        Raises:
            WorkerOfflineError: If connection closes before full frame.
        """
        import struct

        try:
            # Read 4-byte length prefix
            length_bytes = await reader.readexactly(4)
            length = struct.unpack(">I", length_bytes)[0]

            # Read JSON payload
            return await reader.readexactly(length)
        except asyncio.IncompleteReadError as e:
            logger.error(
                f"[UDS_FWD] Remote closed connection mid-read: "
                f"expected={e.expected}, partial={len(e.partial) if e.partial else 0}"
            )
            raise WorkerOfflineError(
                machine_id=machine_id,
                socket_path=socket_path,
                reason="remote_closed",
                original_error=e,
            ) from e
