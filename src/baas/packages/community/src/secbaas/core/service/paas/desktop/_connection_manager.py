"""ConnectionManager for WebSocket session state tracking and request-response correlation.

Manages WebSocket connections from mng daemons, handles duplicate connection detection,
request-response correlation for commands, and connection lifecycle tracking.

Per D-DC01~04: Implements duplicate connection detection with cross-user validation.
Per D-EH05: Cancels pending commands on disconnect via event signaling.
Per D-MP01~05: Provides request-response correlation with request_id generation.
Per D-HT01~05: Heartbeat timeout detection with sweep task.
Per D-CPL01~05: Connection pool limits with hard rejection at 10,000.
"""

from __future__ import annotations

import asyncio
import socket
import time
import uuid
from datetime import datetime
from typing import Any, Protocol

from secbaas.api.paas import ConnectionManager as ConnectionManagerProtocol
from secbaas.core.repository.local_user_machine import (
    LocalUserMachineRepository,
)
from secbaas.logger import get_logger

from ._utils import get_instance_id
from .worker_router import UDSConfig

# WR-01 Fix: Cache server IP at module level to avoid blocking DNS lookup
_SERVER_IP = socket.gethostbyname(socket.gethostname())

logger = get_logger("core-service")


class WebSocketConnection(Protocol):
    """Minimal protocol for a WebSocket handle used by ConnectionManager.

    Only requires send_json() and close() — the two operations
    that ConnectionManager performs on stored connections.
    """

    async def send_json(self, data: dict[str, Any]) -> None: ...
    async def close(self, code: int = 1000) -> None: ...


class ConnectionLimitExceededError(Exception):
    """Raised when connection limit is reached."""

    pass


class ConnectionManager(ConnectionManagerProtocol):
    """Manages WebSocket connections from local mng daemons.

    Provides connection lifecycle management, duplicate detection with async locking,
    and request-response correlation for synchronous command execution over async
    WebSocket transport.

    Architectural Boundary (per D-15.3-01):
        Public API (for LocalPaasService business layer):
        - send_command(): Send command and wait for response
        - send_command_with_request_id(): Send with pre-generated request_id
        - is_connected(): Check connection state
        - initialize(), start(), shutdown(): Lifecycle management
        - is_at_capacity(): Capacity checking
        - clear_stale_route_info(): Clear stale DB route_info during race fallthrough
        - send_callback_result(): Send callback_result frames (WebSocket adapter consumer)

        Internal API (WebSocket layer only):
        - _add_connection(), _remove_connection(): Connection lifecycle callbacks
        - _handle_result(): Result message routing
        - _update_heartbeat(): Heartbeat tracking
        - _on_connect(), _on_disconnect(): Database persistence hooks
        - _get_connection(), _get_user_id(): Internal queries

    Key capabilities:
    - Connection tracking: add, remove, query connections by machine_id
    - Duplicate detection: thread-safe checking with async lock (D-DC01~04)
    - Request correlation: asyncio.Event-based pending request tracking
    - Metadata storage: user_id, remote_addr, timestamps per connection (D-CM01)
    - Cleanup on disconnect: signal pending commands to fail (D-EH05)
    - Heartbeat timeout: automatic sweep task detects stale connections (D-HT01~05)
    - Connection limits: hard limit at 10,000 connections (D-CPL01~05)
    """

    # CR-01 Fix: Use a character that won't appear in validated machine_id
    REQUEST_ID_DELIMITER = "|"

    # D-HT02: Sweep interval 7 seconds
    SWEEP_INTERVAL = 7.0
    # D-HT03: Timeout threshold 30 seconds
    HEARTBEAT_TIMEOUT = 30.0
    # D-CPL01: Maximum connections per instance
    MAX_CONNECTIONS = 10000

    def __init__(
        self,
        repository: LocalUserMachineRepository,
    ) -> None:
        """Initialize ConnectionManager with empty connection pools.

        Args:
            repository: Repository for database operations. Injected by the DI
                container or provided explicitly in tests.
        """
        self._connections: dict[str, WebSocketConnection] = {}
        self._metadata: dict[str, dict] = {}
        # CR-01 Fix: Structured indexing machine_id -> {request_id: event}
        self._pending_requests: dict[str, dict[str, asyncio.Event]] = {}
        self._request_results: dict[str, Any] = {}
        self._lock = asyncio.Lock()

        # D-CPL: Connection limit tracking
        self._connection_count = 0
        self._count_lock = asyncio.Lock()

        # D-HT01: Sweep task infrastructure
        self._sweep_task: asyncio.Task | None = None
        self._shutdown_event = asyncio.Event()

        # D-DB01: Repository infrastructure — injected via constructor
        self._repository: LocalUserMachineRepository = repository
        self._env: str = "dev"
        self._instance_id: str = ""

        # D-09: Single source for socket_path; replaceable via initialize().
        # Default UDSConfig keeps the same /home/admin/secbaas_workers path
        # previously hardcoded in _get_current_route_info.
        self._uds_config: UDSConfig = UDSConfig()

        self._initialized: bool = False

    def _get_current_route_info(self) -> dict:
        """Get current worker route info for database storage.

        Per D-05: Worker ID uses PID only, no hostname.
        Per D-07: Route info contains worker_pid and socket_path only.
        Per D-08: No timestamp in route_info field itself.
        Per D-09: socket_path comes from ``self._uds_config.get_socket_path(pid)``,
        not hardcoded — keeps the path in sync with ``UDSConfig.socket_dir`` so
        a single config knob controls both the UDS server bind and the route_info
        published to the database.

        Returns:
            Dict with worker_pid and socket_path for cross-process routing.
        """
        import os

        pid = os.getpid()
        socket_path = self._uds_config.get_socket_path(pid)

        return {
            "worker_pid": pid,
            "socket_path": socket_path,
        }

    async def _add_connection(
        self,
        machine_id: str,
        websocket: WebSocketConnection,
        metadata: dict | None = None,
    ) -> None:
        """Add a WebSocket connection with thread-safe duplicate check.

        Per D-DC01: Rejects duplicate connections before acceptance.
        Per D-CM01: Stores rich metadata for connection tracking.
        Per D-CPL02: Rejects connections when at capacity.

        Args:
            machine_id: Unique machine identifier from mng daemon.
            websocket: FastAPI WebSocket connection object.
            metadata: Optional connection metadata (user_id, remote_addr, etc.).

        Raises:
            ValueError: If machine_id already has an active connection.
            ConnectionLimitExceededError: If connection limit is reached.
        """
        # D-CPL02: Check connection limit before accepting
        async with self._count_lock:
            if self._connection_count >= self.MAX_CONNECTIONS:
                raise ConnectionLimitExceededError(
                    f"Connection limit reached ({self.MAX_CONNECTIONS})"
                )
            self._connection_count += 1

        try:
            async with self._lock:
                if machine_id in self._connections:
                    raise ValueError(f"Machine {machine_id} already connected")
                self._connections[machine_id] = websocket
                self._metadata[machine_id] = metadata or {}
                # Initialize heartbeat timestamp (only if not provided in metadata)
                if "last_heartbeat" not in self._metadata[machine_id]:
                    self._metadata[machine_id]["last_heartbeat"] = datetime.now()
                logger.debug(f"Connection added for machine_id={machine_id}")
        except Exception:
            # Rollback counter on any error
            self._safe_decrement()
            raise

    def _remove_connection(self, machine_id: str) -> None:
        """Remove connection and cancel pending requests (D-EH05).

        Signals all pending requests for this machine to wake up (they will
        receive a default empty result indicating disconnect).

        Args:
            machine_id: The machine identifier to remove.
        """
        self._connections.pop(machine_id, None)
        self._metadata.pop(machine_id, None)

        # CR-01 Fix: Use structured indexing to find machine-specific requests
        # D-EH05: Cancel pending commands by signaling their events
        machine_requests = self._pending_requests.pop(machine_id, {})
        for request_id, event in machine_requests.items():
            # Set event without storing result - caller will detect disconnect
            event.set()
            logger.debug(f"Signaled pending request {request_id} for disconnect")

        # D-CPL: Decrement counter via fire-and-forget task with error handling (WR-03)
        self._safe_decrement()

        logger.debug(f"Connection removed for machine_id={machine_id}")

    def is_connected(self, machine_id: str) -> bool:
        """Check if machine has active connection.

        Args:
            machine_id: The machine identifier to check.

        Returns:
            True if machine has an active connection, False otherwise.
        """
        return machine_id in self._connections

    def is_at_capacity(self) -> bool:
        """Check if connection limit is reached.

        Returns:
            True if at or over MAX_CONNECTIONS, False otherwise.
        """
        return self._connection_count >= self.MAX_CONNECTIONS

    def clear_stale_route_info(self, machine_id: str) -> None:
        """Clear stale route_info for a machine (CR-02 race-fallthrough hook).

        Public wrapper around ``repository.clear_route_info`` used by
        ``LocalPaasService._route_command`` when the UDS branch detects a
        same-PID race (``route_info["worker_pid"] == os.getpid()`` but
        ``is_connected(machine_id)`` is False). Without this clear, the DB row
        keeps pointing at the now-dead worker until some other heartbeat
        rewrites it, which can leave the machine effectively unreachable
        when the machine never reconnects.

        Best-effort, mirrors the D-14 pattern used in ``_on_disconnect``: any
        DB failure is logged and swallowed so the caller's race-fallthrough
        path is not perturbed by a secondary cleanup error.

        Args:
            machine_id: The machine identifier whose route_info should be cleared.
        """
        try:
            self._repository.clear_route_info(machine_id, self._env)
            logger.info(
                f"[ROUTE_INFO_CLEARED_STALE] Machine {machine_id}: "
                f"cleared during race fallthrough"
            )
        except Exception as e:
            # D-14 pattern: best-effort, log and continue
            logger.warning(f"[ROUTE_INFO_CLEAR_STALE_FAIL] Machine {machine_id}: {e}")

    def initialize(
        self,
        env: str,
        instance_id: str | None = None,
        uds_config: UDSConfig | None = None,
    ) -> None:
        """Initialize ConnectionManager with environment and optional overrides.

        D-DB01: Repository may already be set via constructor (DI container path)
        or provided here for test compatibility.
        D-DB06: Uses ZdasLocalUserMachineRepository from Phase 15.
        D-09: Optionally accepts a ``UDSConfig`` so the wiring layer can make the
        socket-path source explicit. When omitted, the default ``UDSConfig()``
        created in ``__init__`` is preserved (back-compat for existing callers).

        Args:
            env: Environment (dev, pre, prod).
            instance_id: Optional instance ID, auto-detected if None.
            uds_config: Optional UDSConfig for socket_path derivation. When None,
                the previously-set ``self._uds_config`` is kept (default
                ``UDSConfig()`` from ``__init__``).
        """
        self._env = env
        self._instance_id = instance_id or get_instance_id()

        if uds_config is not None:
            self._uds_config = uds_config

        logger.info(
            f"ConnectionManager initialized: instance_id={self._instance_id}, env={self._env}"
        )

        self._initialized = True

    def _on_connect(self, machine_id: str, user_id: str) -> None:
        """Handle database updates on new connection.

        D-DB03: Reject connection on primary status error (fail-fast).
        D-12: Route info write failures are logged but don't reject connection.
        Updates connected_server_instance, status=ONLINE, and route_info.

        Args:
            machine_id: The machine identifier.
            user_id: The user identifier.

        Raises:
            ConnectionError: If repository not initialized.
            Exception: Repositories errors propagate to reject connection.
        """
        if self._repository is None:
            logger.error("ConnectionManager not initialized with repository")
            raise ConnectionError("ConnectionManager not initialized")

        try:
            self._repository.update_instance(machine_id, self._env, self._instance_id)
            self._repository.update_status(machine_id, self._env, "ONLINE")
            logger.info(f"Machine {machine_id} connected: instance={self._instance_id}")
        except Exception:
            logger.error(f"Failed to update connection status for {machine_id}")
            raise

        # Write route_info for cross-process routing (best-effort per D-12)
        try:
            route_info = self._get_current_route_info()
            self._repository.update_route_info(machine_id, self._env, route_info)
            logger.info(
                f"[ROUTE_INFO_WRITTEN] Machine {machine_id}: "
                f"worker_pid={route_info['worker_pid']}, socket_path={route_info['socket_path']}"
            )
        except Exception as e:
            # D-12: Log WARNING but continue - heartbeat will retry
            logger.warning(f"[ROUTE_INFO_WRITE_FAIL] Machine {machine_id}: {e}")

    def _on_disconnect(self, machine_id: str) -> None:
        """Handle database updates on disconnect.

        D-DB05: Updates status=OFFLINE and clears instance with log-only error handling.
        D-10: Clears route_info only if current PID matches stored worker_pid.
        D-14: Route info clear failures are logged but don't block disconnect.
        Idempotent: safe to call multiple times.

        Args:
            machine_id: The machine identifier.
        """
        if self._repository is None:
            return

        try:
            # Check current status to avoid redundant updates (idempotent check)
            record = self._repository.get_by_machine_id(machine_id, self._env)
            if record and record.status == "OFFLINE":
                logger.debug(
                    f"Machine {machine_id} already OFFLINE, skipping disconnect update"
                )
                return

            # Update status to OFFLINE
            self._repository.update_status(machine_id, self._env, "OFFLINE")
            # Clear connected_server_instance
            self._repository.update_instance(machine_id, self._env, "")
            logger.info(f"Machine {machine_id} disconnected: status=OFFLINE")
        except Exception as e:
            # D-DB05: Log only, don't propagate
            logger.warning(f"Disconnect DB update failed for {machine_id}: {e}")
            return  # Skip route_info cleanup on status update failure

        # Clear route_info for cross-process routing (best-effort per D-14)
        try:
            # D-10: Only clear if current PID matches stored worker_pid
            import os

            current_pid = os.getpid()

            route_info = self._repository.get_route_info(machine_id, self._env)
            if route_info and route_info.get("worker_pid") == current_pid:
                self._repository.clear_route_info(machine_id, self._env)
                logger.info(
                    f"[ROUTE_INFO_CLEARED] Machine {machine_id}: "
                    f"cleared by worker_pid={current_pid}"
                )
            elif route_info:
                # Route info belongs to another worker, don't clear
                stored_pid = route_info.get("worker_pid")
                logger.debug(
                    f"[ROUTE_INFO_SKIP_CLEAR] Machine {machine_id}: "
                    f"stored_pid={stored_pid} != current_pid={current_pid}, "
                    f"route_info belongs to another worker"
                )
        except Exception as e:
            # D-14: Log WARNING but continue - cleanup is best-effort
            logger.warning(f"[ROUTE_INFO_CLEAR_FAIL] Machine {machine_id}: {e}")

    @property
    def is_initialized(self) -> bool:
        """Check whether initialize() has been called."""
        return self._initialized

    def ensure_initialized(self) -> None:
        """Idempotent lifecycle bootstrap: initialize + start.

        Combines initialize() and start() into a single convenience method
        used by the application lifecycle (app.py). Reads environment from
        the runtime automatically. Safe to call multiple times — the sweep
        task is only started once.

        Raises:
            RuntimeError: If initialize() or start() fails.
        """
        from secbaas.core.utils.env_utils import get_current_env  # noqa: PLC0415

        logger.info("ConnectionManager initializing...")
        self.initialize(env=get_current_env())
        logger.info("ConnectionManager initiated")
        self._start_sweep()
        logger.info("ConnectionManager started")

    def _start_sweep(self) -> None:
        """Start the background sweep task for heartbeat timeout detection.

        D-HT01: Creates asyncio task for periodic heartbeat checking.
        """
        if self._sweep_task is None or self._sweep_task.done():
            self._sweep_task = asyncio.create_task(self._sweep_loop())
            logger.info("ConnectionManager sweep task started")

    # -- Lifecycle Protocol --------------------------------------------------

    async def start(self) -> None:
        """Lifecycle.start: initialize + start sweep task."""
        self.ensure_initialized()

    async def stop(self) -> None:
        """Lifecycle.stop: graceful shutdown."""
        await self.shutdown()

    async def shutdown(self) -> None:
        """Graceful shutdown: cancel sweep task and close all connections.

        D-GS01: Cancel sweep task immediately (hard shutdown).
        D-GS02: No waiting for pending commands.
        D-GS03: Close all connections with code 1001.
        """
        # D-GS01: Signal shutdown immediately
        self._shutdown_event.set()
        logger.info("ConnectionManager shutdown initiated")

        # Cancel sweep task
        if self._sweep_task and not self._sweep_task.done():
            self._sweep_task.cancel()
            try:
                await self._sweep_task
            except asyncio.CancelledError:
                pass  # Expected

        # D-GS03: Close all connections
        async with self._lock:
            connections = list(self._connections.items())

        close_tasks = [
            self._close_connection(machine_id, ws) for machine_id, ws in connections
        ]
        if close_tasks:
            await asyncio.gather(*close_tasks, return_exceptions=True)

        logger.info("ConnectionManager shutdown complete")

    async def _close_connection(
        self, machine_id: str, websocket: WebSocketConnection
    ) -> None:
        """Close a single connection gracefully.

        Args:
            machine_id: The machine identifier.
            websocket: The WebSocket connection to close.
        """
        try:
            await websocket.close(code=1001)
        except Exception as e:
            logger.warning(f"Error closing connection for {machine_id}: {e}")
        finally:
            # Always clean up, even if close fails
            self._on_disconnect(machine_id)
            self._remove_connection(machine_id)

    async def _update_heartbeat(self, machine_id: str) -> None:
        """Update heartbeat timestamp for a connection.

        D-HT03: Records last heartbeat time for timeout detection.
        D-DB04: Persists to database with log-and-continue error handling.

        Args:
            machine_id: The machine identifier.
        """
        if machine_id not in self._connections:
            logger.warning(f"Heartbeat for unknown machine: {machine_id}")
            return

        # D-DB04: Update database first, then in-memory (log-and-continue on DB error)
        if self._repository is not None:
            try:
                self._repository.update_heartbeat(machine_id, self._env, datetime.now())
            except Exception as e:
                logger.error(f"Heartbeat DB update failed for {machine_id}: {e}")
                # Continue - don't close connection for DB errors

        # Check for instance drift and auto-fix
        if self._repository is not None:
            try:
                record = self._repository.get_by_machine_id(machine_id, self._env)
                if record and record.connected_server_instance != self._instance_id:
                    logger.warning(
                        f"[INSTANCE_MISMATCH] Machine {machine_id}: "
                        f"DB points to {record.connected_server_instance}, "
                        f"but actual connection is on {self._instance_id}. Auto-fixing..."
                    )
                    # Auto-fix: update database to point to current instance
                    self._repository.update_instance(
                        machine_id, self._env, self._instance_id
                    )
                    # Also ensure status is ONLINE
                    self._repository.update_status(machine_id, self._env, "ONLINE")
                    logger.info(
                        f"[INSTANCE_MISMATCH] Machine {machine_id}: "
                        f"Database updated to {self._instance_id}, status set to ONLINE"
                    )
            except Exception as e:
                logger.error(f"Instance drift check/fix failed for {machine_id}: {e}")

        # D-13 lazy refresh — SELECT, compare, conditionally UPDATE.
        # Avoids high-concurrency UPDATE hot-spot when route_info hasn't changed
        # between heartbeats. No in-memory cache (D-15) — DB stays single source.
        #
        # WR-04 Fix: do the SELECT outside the lock (DB IO must not hold the
        # asyncio lock that serializes connection-state mutations), then re-check
        # `machine_id in self._connections` under the lock and perform the write
        # plus the metadata heartbeat refresh in one atomic-from-asyncio-POV
        # critical section. If the machine disconnected between the SELECT and
        # the lock acquire, skip both the route_info UPDATE and the heartbeat
        # metadata update — the connection is gone and a disconnect-in-progress
        # may have already cleared route_info; re-stamping it here would
        # recreate the CR-02 stuck-row scenario.
        current_route_info: dict | None = None
        stored_route_info: dict | None = None
        select_failed = False
        if self._repository is not None:
            try:
                current_route_info = self._get_current_route_info()
                stored_route_info = self._repository.get_route_info(
                    machine_id, self._env
                )
            except Exception as e:
                # D-13: Log ERROR but continue - best-effort refresh
                logger.error(f"[ROUTE_INFO_REFRESH_FAIL] Machine {machine_id}: {e}")
                select_failed = True

        async with self._lock:
            # WR-04: re-check membership inside lock — connection may have
            # been torn down during the awaits above.
            if machine_id not in self._connections:
                logger.debug(
                    f"[ROUTE_INFO_SKIP_REFRESH] Machine {machine_id}: "
                    f"disconnected during heartbeat, skipping route_info refresh"
                )
                return

            if self._repository is not None and not select_failed:
                try:
                    if stored_route_info == current_route_info:
                        logger.debug(
                            f"[ROUTE_INFO_SKIPPED] Machine {machine_id}: "
                            f"stored matches current, skipping UPDATE "
                            f"(worker_pid={current_route_info['worker_pid']})"
                        )
                    else:
                        self._repository.update_route_info(
                            machine_id, self._env, current_route_info
                        )
                        logger.debug(
                            f"[ROUTE_INFO_REFRESHED] Machine {machine_id}: "
                            f"worker_pid={current_route_info['worker_pid']}, "
                            f"socket_path={current_route_info['socket_path']}, "
                            f"stored={stored_route_info}"
                        )
                except Exception as e:
                    # D-13: Log ERROR but continue - best-effort refresh
                    logger.error(f"[ROUTE_INFO_REFRESH_FAIL] Machine {machine_id}: {e}")

            if machine_id in self._metadata:
                self._metadata[machine_id]["last_heartbeat"] = datetime.now()
                logger.debug(f"Heartbeat updated for machine_id={machine_id}")

    async def _sweep_loop(self) -> None:
        """Background task that periodically checks for stale connections.

        D-HT01: Runs continuously until shutdown event is set.
        D-HT02: Checks every SWEEP_INTERVAL seconds (7s).
        """
        while not self._shutdown_event.is_set():
            try:
                await self._check_timeouts()
                # Wait for shutdown event or timeout
                await asyncio.wait_for(
                    self._shutdown_event.wait(), timeout=self.SWEEP_INTERVAL
                )
            except TimeoutError:
                # Normal interval expiration, continue loop
                continue
            except asyncio.CancelledError:
                # Task cancelled, exit cleanly
                break
            except Exception as e:
                logger.error(f"Sweep loop error: {e}")
                await asyncio.sleep(1)

    async def _check_timeouts(self) -> None:
        """Check all connections for heartbeat timeout.

        D-HT03: Detects connections with heartbeat older than HEARTBEAT_TIMEOUT (30s).
        Collects stale connections under lock, closes outside lock to avoid blocking.
        """
        now = datetime.now()
        timeouts: list[tuple[str, WebSocketConnection]] = []

        async with self._lock:
            for machine_id, meta in self._metadata.items():
                last_heartbeat = meta.get("last_heartbeat")
                if last_heartbeat is not None:
                    elapsed = (now - last_heartbeat).total_seconds()
                    if elapsed > self.HEARTBEAT_TIMEOUT:
                        websocket = self._connections.get(machine_id)
                        if websocket is not None:
                            timeouts.append((machine_id, websocket))

        # Close stale connections outside lock to avoid blocking
        for machine_id, websocket in timeouts:
            await self._close_stale_connection(machine_id, websocket)

    async def _close_stale_connection(
        self, machine_id: str, websocket: WebSocketConnection
    ) -> None:
        """Close a stale connection and clean up.

        D-HT04: Immediately close WebSocket with code 1001 (Going Away).
        D-HT05: Update status to OFFLINE via on_disconnect before remove_connection.

        Args:
            machine_id: The machine identifier.
            websocket: The WebSocket connection to close.
        """
        try:
            await websocket.close(code=1001)
            logger.warning(f"Closing stale connection for machine_id={machine_id}")
        except Exception as e:
            logger.warning(f"Error closing stale connection for {machine_id}: {e}")
        finally:
            # D-HT05: Update database status before removing from in-memory
            try:
                self._on_disconnect(machine_id)
            except Exception as e:
                logger.warning(
                    f"Error in on_disconnect for stale connection {machine_id}: {e}"
                )
            self._remove_connection(machine_id)

    async def _decrement_counter(self) -> None:
        """Decrement connection count safely.

        D-CPL: Used for fire-and-forget counter decrement on disconnect.
        """
        async with self._count_lock:
            self._connection_count = max(0, self._connection_count - 1)
            logger.debug(f"Connection count decremented to {self._connection_count}")

    def _safe_decrement(self) -> None:
        """Fire-and-forget wrapper with error handling for counter decrement.

        WR-03 Fix: Wraps _decrement_counter to ensure exceptions are logged
        rather than lost as 'Task exception was never retrieved'.
        """

        async def _decrement_wrapper() -> None:
            try:
                await self._decrement_counter()
            except Exception as e:
                logger.error(f"Failed to decrement connection counter: {e}")

        asyncio.create_task(_decrement_wrapper())

    def _get_connection(self, machine_id: str) -> WebSocketConnection | None:
        """Get WebSocket connection for a machine.

        Args:
            machine_id: The machine identifier.

        Returns:
            WebSocket object or None if not connected.
        """
        return self._connections.get(machine_id)

    def _get_user_id(self, machine_id: str) -> str | None:
        """Get user_id from connection metadata (D-DC04 cross-user check).

        Args:
            machine_id: The machine identifier.

        Returns:
            user_id from metadata or None if not found.
        """
        meta = self._metadata.get(machine_id)
        return meta.get("user_id") if meta else None

    async def send_command(self, machine_id: str, command: dict) -> dict:
        """Send command and wait for result (R3.6 request-response correlation).

        Implements D-MP01 correlation pattern:
        1. Generate unique request_id with machine_id prefix
        2. Register pending request with asyncio.Event
        3. Send command via WebSocket
        4. Wait for result (timeout handled via asyncio.wait_for)
        5. Return result or raise on timeout/disconnect

        Args:
            machine_id: Target machine identifier.
            command: Command payload dict to send.

        Returns:
            Result dict from mng daemon response.

        Raises:
            ConnectionError: If machine is not connected.
            TimeoutError: If command times out (30s default).
        """
        request_id = f"{machine_id}{self.REQUEST_ID_DELIMITER}{uuid.uuid4().hex}"
        return await self.send_command_with_request_id(machine_id, command, request_id)

    async def send_command_with_request_id(
        self, machine_id: str, command: dict, request_id: str
    ) -> dict:
        """Send command with pre-generated request_id (for forwarded requests).

        This method is used by the internal forward endpoint to send commands
        using a request_id provided by the originating instance. This ensures
        correlation works across instance boundaries for distributed tracing.

        Differences from send_command:
        - Uses provided request_id instead of generating one
        - No machine_id prefix validation (request_id already correlated)

        Args:
            machine_id: Target machine identifier.
            command: Command payload dict to send.
            request_id: Pre-generated request ID for correlation.

        Returns:
            Result dict from mng daemon response.

        Raises:
            ConnectionError: If machine is not connected.
            TimeoutError: If command times out (30s default).
        """
        start_time = time.time()
        action = command.get("action", "unknown")

        async with self._lock:
            websocket = self._connections.get(machine_id)
            if not websocket:
                logger.warning(
                    f"[CMD_REJECT] Machine not connected: "
                    f"machine_id={machine_id}, action={action}, request_id={request_id}"
                )
                raise ConnectionError(f"Machine {machine_id} not connected")

            event = asyncio.Event()

            # Store in structured dict machine_id -> {request_id: event}
            if machine_id not in self._pending_requests:
                self._pending_requests[machine_id] = {}
            self._pending_requests[machine_id][request_id] = event

            # Send command while holding lock to ensure connection is still valid
            logger.info(
                f"[CMD_PAYLOAD] Sending command to mng: machine_id={machine_id}, "
                f"action={action}, request_id={request_id}, command={command}"
            )
            await websocket.send_json(
                {"type": "command", "request_id": request_id, "payload": command}
            )
            logger.info(
                f"[CMD_SEND] Command sent: machine_id={machine_id}, "
                f"action={action}, request_id={request_id}"
            )

        try:
            # Wait for result with timeout (outside lock)
            await asyncio.wait_for(event.wait(), timeout=30.0)

            # Return and remove result (empty dict if disconnect or no result)
            result = self._request_results.pop(request_id, {})
            duration_ms = (time.time() - start_time) * 1000
            logger.info(
                f"[CMD_RESULT] Received result from mng: machine_id={machine_id}, "
                f"action={action}, request_id={request_id}, result={result}"
            )
            logger.info(
                f"[CMD_SUCCESS] Command completed: machine_id={machine_id}, "
                f"action={action}, request_id={request_id}, duration_ms={duration_ms:.1f}"
            )
            return result

        except TimeoutError:
            duration_ms = (time.time() - start_time) * 1000
            logger.warning(
                f"[CMD_TIMEOUT] Command timeout: "
                f"machine_id={machine_id}, action={action}, "
                f"request_id={request_id}, duration_ms={duration_ms:.1f}"
            )
            raise TimeoutError(f"Command timeout for {machine_id}")
        finally:
            # Clean up pending request
            async with self._lock:
                if machine_id in self._pending_requests:
                    self._pending_requests[machine_id].pop(request_id, None)
                    if not self._pending_requests[machine_id]:
                        self._pending_requests.pop(machine_id, None)

    def _handle_result(self, message: dict) -> None:
        """Handle incoming result message (R3.7 result routing).

        Per D-MP01: Correlates incoming result with pending request via request_id.
        Stores result payload and signals waiting event to unblock send_command.

        Args:
            message: Result message dict containing request_id and payload.
        """
        request_id = message.get("request_id")
        if not request_id:
            logger.warning("[RESULT_REJECT] Result message missing request_id")
            return

        # CR-01 Fix: Extract machine_id from request_id to look up in structured dict
        # request_id format: "machine_id|uuid"
        if self.REQUEST_ID_DELIMITER not in request_id:
            logger.warning(f"[RESULT_REJECT] Invalid request_id format: {request_id}")
            return

        machine_id = request_id.split(self.REQUEST_ID_DELIMITER)[0]
        payload = message.get("payload", {})
        status = payload.get("status", "unknown")

        # Look up in structured pending_requests
        machine_requests = self._pending_requests.get(machine_id, {})
        if request_id in machine_requests:
            self._request_results[request_id] = payload
            machine_requests[request_id].set()
            logger.info(
                f"[RESULT_HANDLED] Result processed: machine_id={machine_id}, "
                f"request_id={request_id}, status={status}"
            )
        else:
            # WR-02 Fix: Log warning for unknown/expired requests
            logger.warning(
                f"[RESULT_ORPHAN] Result for unknown/expired request: "
                f"machine_id={machine_id}, request_id={request_id}, status={status}"
            )

    async def send_callback_result(
        self,
        machine_id: str,
        request_id: str,
        status: str,
        data: dict | None = None,
        error: str | None = None,
        message: str | None = None,
    ) -> bool:
        """Send callback_result message to mng daemon (request-response callback pattern).

        Implements the callback_result message type per LOCAL_MNG_WEBSOCKET_PROTOCOL.md
        Section 3.4. Used when callbacks require responses (request-response mode).

        Args:
            machine_id: Target machine identifier.
            request_id: The request_id from the original callback invocation.
            status: "ok" for success, "error" for failure.
            data: Optional data dict for successful responses (status="ok").
            error: Optional error code for failed responses (status="error").
            message: Optional error message for failed responses (status="error").

        Returns:
            True if message was sent successfully, False if machine not connected
            or send failed.
        """
        websocket = self._connections.get(machine_id)
        if not websocket:
            logger.warning(
                f"[CALLBACK_SEND_FAILED] Machine not connected: "
                f"machine_id={machine_id}, request_id={request_id}"
            )
            return False

        # Build payload with status always included
        payload: dict[str, Any] = {"status": status}

        # Add data for success responses
        if status == "ok" and data is not None:
            payload["data"] = data

        # Add error details for error responses
        if status == "error":
            if error is not None:
                payload["error"] = error
            if message is not None:
                payload["message"] = message

        try:
            await websocket.send_json(
                {
                    "type": "callback_result",
                    "request_id": request_id,
                    "payload": payload,
                }
            )
            logger.info(
                f"[CALLBACK_RESULT_SENT] Callback result sent: "
                f"machine_id={machine_id}, request_id={request_id}, status={status}"
            )
            return True
        except Exception as e:
            logger.error(
                f"[CALLBACK_SEND_ERROR] Failed to send callback result: "
                f"machine_id={machine_id}, request_id={request_id}, status={status}, error={e}"
            )
            return False
