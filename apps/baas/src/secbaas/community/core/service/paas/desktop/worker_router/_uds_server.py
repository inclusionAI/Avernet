"""UDS (Unix Domain Socket) Server for cross-process mng forwarding."""

import asyncio
import json
import os
import socket
import struct
from pathlib import Path
from typing import Any

from secbaas.community.logger import get_logger

from ._models import UDSConfig

logger = get_logger("core-service")


class UDSServer:
    """Asyncio UDS server for receiving forwarded mng commands.

    Per D-24: Single-threaded asyncio server.
    Per D-03: TCP-like connection test for orphan detection.
    Per D-04: Aggressive cleanup at startup.
    """

    def __init__(
        self,
        config: UDSConfig | None = None,
        connection_manager: Any | None = None,
    ) -> None:
        """Initialize UDS server.

        Args:
            config: UDS configuration. Uses defaults if None.
            connection_manager: Reference for dispatching commands (Phase 32).
        """
        self._config = config or UDSConfig()
        self._connection_manager = connection_manager
        self._server: asyncio.AbstractServer | None = None
        self._socket_path: str | None = None
        self._shutdown_event = asyncio.Event()

    @property
    def socket_path(self) -> str | None:
        """Get current socket path."""
        return self._socket_path

    def _is_socket_alive(self, socket_path: str) -> bool:
        """Test if socket file belongs to a live process.

        Per D-03: TCP-like connection test.
        Per D-06: ConnectionRefused or FileNotFound = dead/orphan.

        Returns:
            True if socket is accepting connections (process alive).
            False if socket is orphan (no process listening).
        """
        try:
            test_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            test_sock.settimeout(1.0)
            test_sock.connect(socket_path)
            test_sock.close()
            return True
        except (ConnectionRefusedError, FileNotFoundError, OSError):
            return False
        except Exception as e:  # noqa: BLE001
            logger.debug(
                f"[UDS_ORPHAN_TEST] Unexpected error testing {socket_path}: {e}"
            )
            return False

    def _cleanup_orphan_sockets(self) -> None:
        """Remove orphaned socket files at startup.

        Per D-04: Aggressive cleanup - delete sockets that fail connection test.
        Per D-15: Main cleanup happens at worker startup.
        """
        socket_dir = Path(self._config.socket_dir)

        if not socket_dir.exists():
            logger.info(f"[UDS_CLEANUP] Socket directory does not exist: {socket_dir}")
            return

        removed_count = 0
        for sock_file in socket_dir.glob("worker_*.sock"):
            if self._is_socket_alive(str(sock_file)):
                logger.debug(f"[UDS_CLEANUP] Socket alive, keeping: {sock_file}")
            else:
                try:
                    sock_file.unlink()
                    removed_count += 1
                    logger.info(f"[UDS_CLEANUP] Removed orphan socket: {sock_file}")
                except OSError as e:
                    logger.warning(f"[UDS_CLEANUP] Failed to remove {sock_file}: {e}")

        if removed_count > 0:
            logger.info(f"[UDS_CLEANUP] Removed {removed_count} orphan socket(s)")

    async def start(self) -> str:
        """Start the UDS server.

        Steps:
        1. Cleanup orphan sockets (D-15)
        2. Create socket directory
        3. Create socket server with current PID

        Returns:
            Socket path string.

        Raises:
            OSError: If socket bind fails (socket truly occupied per D-04).
        """
        # Step 1: Cleanup orphans (D-15)
        self._cleanup_orphan_sockets()

        # Step 2: Create directory
        socket_dir = Path(self._config.socket_dir)
        try:
            socket_dir.mkdir(parents=True, exist_ok=True, mode=0o700)  # noqa: ASYNC240
        except OSError as e:
            logger.error(
                f"[UDS_START] Failed to create socket directory: {socket_dir}: {e}"
            )
            raise

        # Step 3: Start server with PID-based socket path (D-01)
        pid = os.getpid()
        self._socket_path = self._config.get_socket_path(pid)

        logger.info(
            f"[UDS_START] Starting UDS server: pid={pid}, "
            f"socket_path={self._socket_path}"
        )

        # CR-02 Fix: Preemptively unlink our own socket path before binding.
        # If previous process died without cleanup, remove stale socket.
        # If another server is truly running on this path, bind will fail below.
        try:
            os.unlink(self._socket_path)
            logger.info(f"[UDS_START] Removed stale socket: {self._socket_path}")
        except FileNotFoundError:
            pass  # Socket didn't exist, that's fine

        try:
            # CR-03 Fix: Use umask to set permissions atomically at creation time.
            # This prevents a window where socket has default permissions.
            old_umask = os.umask(0o177)  # 0o600 = 0o777 - 0o177
            try:
                self._server = await asyncio.start_unix_server(
                    self._handle_client,
                    path=self._socket_path,
                    backlog=self._config.listen_backlog,
                )
            finally:
                os.umask(old_umask)

            # Permissions are already 0o600 from umask, but verify.
            # Guard against race where socket file doesn't exist yet.
            try:
                actual_mode = os.stat(self._socket_path).st_mode & 0o777
            except FileNotFoundError:
                logger.warning(
                    f"[UDS_START] Socket {self._socket_path} not found after bind, "
                    "skipping permission verification"
                )
            else:
                if actual_mode != self._config.socket_mode:
                    os.chmod(self._socket_path, self._config.socket_mode)

            logger.info(
                f"[UDS_START] Server started successfully: "
                f"socket_path={self._socket_path}, mode={oct(self._config.socket_mode)}"
            )

            return self._socket_path

        except OSError as e:
            logger.error(f"[UDS_START] Failed to bind socket {self._socket_path}: {e}")
            raise

    async def stop(self) -> None:
        """Stop the UDS server and cleanup socket file.

        Closes server and removes socket file on normal exit.
        """
        logger.info(f"[UDS_STOP] Stopping UDS server: socket_path={self._socket_path}")

        if self._server:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

        if self._socket_path and os.path.exists(self._socket_path):  # noqa: ASYNC240
            try:
                os.unlink(self._socket_path)
                logger.info(f"[UDS_STOP] Socket file removed: {self._socket_path}")
            except OSError as e:
                logger.warning(f"[UDS_STOP] Failed to remove socket file: {e}")

        self._socket_path = None
        logger.info("[UDS_STOP] Server stopped")

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        """Handle incoming UDS client connection (receiving worker).

        Per D-06: Frame format [4-byte big-endian length][UTF-8 JSON]
        Per D-07: Extract envelope and forward to ConnectionManager
        Per D-12: Use ConnectionManager for actual WebSocket dispatch
        Per D-13: Mirror instance router lookup pattern

        Flow:
        1. Read framed request
        2. Verify target_worker_pid matches current PID (security check)
        3. Extract machine_id from envelope
        4. Dispatch via ConnectionManager.send_command_with_request_id()
        5. Format and send framed response
        """
        client_addr = writer.get_extra_info("peername")
        current_pid = os.getpid()

        logger.debug(f"[UDS_SERVER] Connection from: {client_addr}")

        if self._connection_manager is None:
            logger.error("[UDS_SERVER] No ConnectionManager available")
            await self._send_error_response(
                writer,
                "SERVER_NOT_READY",
                "UDS server not initialized with ConnectionManager",
            )
            return

        try:
            # Step 1: Read framed request
            request_bytes = await self._read_framed_data(reader)
            request = json.loads(request_bytes.decode("utf-8"))

            # Step 2: Verify target_worker_pid (security per D-07)
            target_pid = request.get("target_worker_pid")
            if target_pid != current_pid:
                logger.warning(
                    f"[UDS_SERVER] PID mismatch: expected={current_pid}, got={target_pid}"
                )
                await self._send_error_response(
                    writer,
                    "PID_MISMATCH",
                    f"This worker is PID {current_pid}, request was for PID {target_pid}",
                )
                return

            # Step 3: Extract envelope
            envelope = request.get("envelope", {})
            machine_id = envelope.get("machine_id")
            action = envelope.get("action")
            params = envelope.get("params", {})
            request_id = envelope.get("request_id")

            if not machine_id:
                await self._send_error_response(
                    writer, "MISSING_MACHINE_ID", "envelope.machine_id is required"
                )
                return

            logger.info(
                f"[UDS_SERVER] Dispatching: machine_id={machine_id}, "
                f"action={action}, request_id={request_id}"
            )

            # Step 4: Verify we have the connection locally (per D-12, D-13)
            if not self._connection_manager.is_connected(machine_id):
                logger.warning(
                    f"[UDS_SERVER] Machine {machine_id} not connected to this worker"
                )
                await self._send_error_response(
                    writer,
                    "MACHINE_NOT_CONNECTED",
                    f"Machine {machine_id} is not connected to this worker process",
                )
                return

            # Step 5: Build command and dispatch via ConnectionManager
            command = {"action": action, "params": params}

            try:
                if request_id:
                    # Use send_command_with_request_id to preserve request_id for tracing
                    result = (
                        await self._connection_manager.send_command_with_request_id(
                            machine_id, command, request_id
                        )
                    )
                else:
                    # Generate new request_id
                    result = await self._connection_manager.send_command(
                        machine_id, command
                    )

                logger.info(
                    f"[UDS_SERVER] Command succeeded: machine_id={machine_id}, "
                    f"action={action}, request_id={request_id}"
                )

                # Step 6: Send success response.
                # D-01: raw pass-through of the mng payload — no
                # `{status:"ok", data:...}` envelope wrapper. This aligns the
                # UDS forward contract with `internal_router.internal_forward`
                # (HTTP cross-instance path) and `ConnectionManager.send_command`
                # (local path), both of which return the raw mng dict directly.
                # T-35-01-03: defensively wrap a non-dict result as
                # `{"result": result}` so json.dumps cannot fail and the frame
                # stays a valid JSON object the upstream caller can parse.
                # WR-06: surface the latent contract violation by logging a
                # WARNING whenever the defensive wrap fires — none of today's
                # callers return non-dict, so any occurrence is a regression
                # signal worth investigating.
                if not isinstance(result, dict):
                    logger.warning(
                        f"[UDS_SERVER] Non-dict result from send_command, wrapping: "
                        f"type={type(result).__name__}, machine_id={machine_id}, "
                        f"action={action}, request_id={request_id}"
                    )
                    payload = {"result": result}
                else:
                    payload = result
                await self._send_framed_response(writer, payload)

            except Exception as e:
                logger.error(
                    f"[UDS_SERVER] Command failed: machine_id={machine_id}, "
                    f"action={action}, error={e}"
                )
                await self._send_error_response(writer, "COMMAND_FAILED", str(e))

        except asyncio.IncompleteReadError:
            logger.warning("[UDS_SERVER] Client disconnected during read")
        except json.JSONDecodeError as e:
            logger.error(f"[UDS_SERVER] Invalid JSON: {e}")
            await self._send_error_response(
                writer, "INVALID_JSON", f"JSON parse error: {e}"
            )
        except Exception as e:
            # D-03: consolidate the previous 'internal_error' code into
            # COMMAND_FAILED so envelope.error stays within the dedicated
            # UPPER_SNAKE family (no lowercase mixed forms).
            logger.error(f"[UDS_SERVER] Unexpected error: {e}")
            await self._send_error_response(writer, "COMMAND_FAILED", str(e))
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
            logger.debug(f"[UDS_SERVER] Connection closed: {client_addr}")

    async def _read_framed_data(self, reader: asyncio.StreamReader) -> bytes:
        """Read framed data: [4-byte length][payload]."""
        length_bytes = await reader.readexactly(4)
        length = struct.unpack(">I", length_bytes)[0]
        return await reader.readexactly(length)

    async def _send_framed_response(
        self, writer: asyncio.StreamWriter, response: dict
    ) -> None:
        """Send framed JSON response."""
        json_bytes = json.dumps(response).encode("utf-8")
        frame = struct.pack(">I", len(json_bytes)) + json_bytes
        writer.write(frame)
        await writer.drain()

    async def _send_error_response(
        self, writer: asyncio.StreamWriter, error_code: str, message: str
    ) -> None:
        """Send error response frame."""
        await self._send_framed_response(
            writer, {"status": "error", "error": error_code, "message": message}
        )
