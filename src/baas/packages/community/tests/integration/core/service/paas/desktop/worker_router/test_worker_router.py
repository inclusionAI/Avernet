"""Tests for WorkerRouter integration.

D-20: WorkerRouter as independent component.
D-23: UDS forwarding errors, no fallback.
"""

import asyncio
import json
import os
import struct
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.integration

from secbaas.core.service.paas.desktop.worker_router import (
    WorkerRouter,
)
from secbaas.core.service.paas.desktop.worker_router._exceptions import (
    RouteNotFoundError,
    WorkerOfflineError,
    WorkerRouterError,
)
from secbaas.core.service.paas.desktop.worker_router._models import (
    UDSConfig,
    WorkerRouteInfo,
)


class TestWorkerRouter:
    """Tests for WorkerRouter main functionality."""

    @pytest.fixture
    def mock_repository(self):
        return MagicMock()

    @pytest.fixture
    def mock_connection_manager(self):
        cm = MagicMock()
        cm.is_connected = MagicMock(return_value=False)
        return cm

    @pytest.fixture
    def temp_config(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            yield UDSConfig(socket_dir=tmpdir, socket_mode=0o600)

    @pytest.fixture
    def router(self, mock_repository, mock_connection_manager, temp_config):
        """Create WorkerRouter with mocked dependencies and temp socket dir."""
        return WorkerRouter(
            repository=mock_repository,
            connection_manager=mock_connection_manager,
            config=temp_config,
        )

    @pytest.fixture
    def router_no_cm(self, mock_repository, temp_config):
        """Create WorkerRouter with no connection_manager (None)."""
        return WorkerRouter(
            repository=mock_repository,
            connection_manager=None,
            config=temp_config,
        )

    def _make_route_info(
        self,
        worker_pid: int = 12345,
        socket_path: str = "/tmp/worker_12345.sock",
    ) -> WorkerRouteInfo:
        """Helper to create valid WorkerRouteInfo."""
        return WorkerRouteInfo(worker_pid=worker_pid, socket_path=socket_path)

    # ---- Properties ----

    def test_socket_path_property(self, router):
        """socket_path property returns UDS socket path."""
        path = router.socket_path
        # Before start, socket_path is None
        assert path is None

    def test_worker_pid_property(self, router):
        """worker_pid property returns current process PID."""
        assert router.worker_pid == os.getpid()

    # ---- start / stop ----

    @pytest.mark.asyncio
    async def test_start_exposes_socket_path(self, router):
        """Start exposes socket path via property with current PID."""
        await router.start()

        socket_path = router.socket_path
        assert socket_path is not None
        assert f"worker_{os.getpid()}.sock" in socket_path
        assert os.path.exists(socket_path)

        # Cleanup
        await router.stop()

    @pytest.mark.asyncio
    async def test_stop_removes_socket(self, router):
        """Stop removes socket file."""
        await router.start()
        socket_path = router.socket_path
        assert socket_path is not None
        assert os.path.exists(socket_path)

        await router.stop()
        assert router.socket_path is None
        assert not os.path.exists(socket_path)

    # ---- get_route_for_machine ----

    def test_get_route_for_machine_returns_info(self, router, mock_repository):
        """get_route_for_machine returns WorkerRouteInfo."""
        mock_repository.get_route_info.return_value = {
            "worker_pid": 12345,
            "socket_path": "/tmp/worker_12345.sock",
        }

        result = router.get_route_for_machine("machine-123", "dev")

        assert result["worker_pid"] == 12345
        assert result["socket_path"] == "/tmp/worker_12345.sock"

    def test_get_route_for_machine_raises_when_null(self, router, mock_repository):
        """get_route_for_machine raises RouteNotFoundError when route_info is None."""
        mock_repository.get_route_info.return_value = None

        with pytest.raises(RouteNotFoundError) as exc_info:
            router.get_route_for_machine("machine-123", "dev")

        assert "machine-123" in str(exc_info.value)

    def test_get_route_for_machine_raises_on_repo_error(self, router, mock_repository):
        """get_route_for_machine raises WorkerRouterError on repository exception."""
        mock_repository.get_route_info.side_effect = RuntimeError("DB down")

        with pytest.raises(WorkerRouterError) as exc_info:
            router.get_route_for_machine("machine-456", "dev")

        assert "machine-456" in str(exc_info.value)
        assert "Failed to get route info" in str(exc_info.value)

    def test_get_route_for_machine_raises_on_invalid_route_info(
        self, router, mock_repository
    ):
        """get_route_for_machine raises WorkerRouterError when route_info missing fields."""
        mock_repository.get_route_info.return_value = {"worker_pid": 12345}
        # Missing "socket_path"

        with pytest.raises(WorkerRouterError) as exc_info:
            router.get_route_for_machine("machine-789", "dev")

        assert "Invalid route_info format" in str(exc_info.value)
        assert "machine-789" in str(exc_info.value)

    # ---- should_handle_locally ----

    def test_should_handle_locally_true_when_connected(
        self, router, mock_connection_manager
    ):
        """should_handle_locally returns True when ConnectionManager shows connected."""
        mock_connection_manager.is_connected.return_value = True

        result = router.should_handle_locally("machine-123")

        assert result is True
        mock_connection_manager.is_connected.assert_called_once_with("machine-123")

    def test_should_handle_locally_false_when_not_connected(
        self, router, mock_connection_manager
    ):
        """should_handle_locally returns False when machine not in this worker."""
        mock_connection_manager.is_connected.return_value = False

        result = router.should_handle_locally("machine-123")

        assert result is False

    def test_should_handle_locally_false_when_cm_is_none(self, router_no_cm):
        """should_handle_locally returns False when connection_manager is None."""
        result = router_no_cm.should_handle_locally("machine-123")
        assert result is False

    # ---- forward_to_worker ----

    @pytest.mark.asyncio
    async def test_forward_to_worker_success(self, router):
        """forward_to_worker sends command and receives response."""
        route_info = self._make_route_info()
        command = {"action": "restart", "params": {"force": True}}

        response_payload = {"status": "ok", "data": "restarted"}
        response_json = json.dumps(response_payload).encode("utf-8")

        mock_reader = self._make_stream_reader(response_json)
        mock_writer = self._make_stream_writer()

        with patch("asyncio.open_unix_connection") as mock_open:
            mock_open.return_value = (mock_reader, mock_writer)

            result = await router.forward_to_worker(
                machine_id="machine-123",
                command=command,
                route_info=route_info,
            )

        assert result == response_payload
        assert mock_open.called

    @pytest.mark.asyncio
    async def test_forward_to_worker_connection_refused(self, router):
        """forward_to_worker raises WorkerOfflineError on connection refused."""
        route_info = self._make_route_info()
        command = {"action": "restart"}

        with patch("asyncio.open_unix_connection") as mock_open:
            mock_open.side_effect = ConnectionRefusedError("No listener")

            with pytest.raises(WorkerOfflineError) as exc_info:
                await router.forward_to_worker(
                    machine_id="machine-123",
                    command=command,
                    route_info=route_info,
                )

        assert exc_info.value.reason == "connection_refused"
        assert exc_info.value.machine_id == "machine-123"

    @pytest.mark.asyncio
    async def test_forward_to_worker_file_not_found(self, router):
        """forward_to_worker raises WorkerOfflineError on FileNotFoundError."""
        route_info = self._make_route_info()
        command = {"action": "restart"}

        with patch("asyncio.open_unix_connection") as mock_open:
            mock_open.side_effect = FileNotFoundError("No socket")

            with pytest.raises(WorkerOfflineError) as exc_info:
                await router.forward_to_worker(
                    machine_id="machine-123",
                    command=command,
                    route_info=route_info,
                )

        assert exc_info.value.reason == "connection_refused"

    @pytest.mark.asyncio
    async def test_forward_to_worker_connect_timeout(self, router):
        """forward_to_worker raises WorkerOfflineError on connect timeout."""
        route_info = self._make_route_info()
        command = {"action": "restart"}

        with patch("asyncio.open_unix_connection") as mock_open:
            mock_open.side_effect = TimeoutError("Connect timed out")

            with pytest.raises(WorkerOfflineError) as exc_info:
                await router.forward_to_worker(
                    machine_id="machine-123",
                    command=command,
                    route_info=route_info,
                )

        assert exc_info.value.reason == "connect_timeout"

    @pytest.mark.asyncio
    async def test_forward_to_worker_os_error(self, router):
        """forward_to_worker raises WorkerOfflineError on OSError."""
        route_info = self._make_route_info()
        command = {"action": "restart"}

        with patch("asyncio.open_unix_connection") as mock_open:
            mock_open.side_effect = OSError("Permission denied")

            with pytest.raises(WorkerOfflineError) as exc_info:
                await router.forward_to_worker(
                    machine_id="machine-123",
                    command=command,
                    route_info=route_info,
                )

        assert "os_error" in exc_info.value.reason

    @pytest.mark.asyncio
    async def test_forward_to_worker_response_timeout(self, router):
        """forward_to_worker raises WorkerOfflineError on response timeout."""
        route_info = self._make_route_info()
        command = {"action": "restart"}

        mock_reader = MagicMock()
        mock_reader.readexactly = AsyncMock(
            side_effect=TimeoutError("Response timed out")
        )
        mock_writer = self._make_stream_writer()

        with patch("asyncio.open_unix_connection") as mock_open:
            mock_open.return_value = (mock_reader, mock_writer)

            with pytest.raises(WorkerOfflineError) as exc_info:
                await router.forward_to_worker(
                    machine_id="machine-123",
                    command=command,
                    route_info=route_info,
                    timeout=1.0,
                )

        assert exc_info.value.reason == "response_timeout"

    @pytest.mark.asyncio
    async def test_forward_to_worker_invalid_json(self, router):
        """forward_to_worker raises WorkerRouterError on invalid JSON response."""
        route_info = self._make_route_info()
        command = {"action": "restart"}

        # Return non-JSON bytes (correctly framed)
        garbage = b"not-json-response"

        mock_reader = self._make_stream_reader(garbage)
        mock_writer = self._make_stream_writer()

        with patch("asyncio.open_unix_connection") as mock_open:
            mock_open.return_value = (mock_reader, mock_writer)

            with pytest.raises(WorkerRouterError) as exc_info:
                await router.forward_to_worker(
                    machine_id="machine-123",
                    command=command,
                    route_info=route_info,
                )

        assert "Invalid response from worker" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_forward_to_worker_remote_closed_mid_read(self, router):
        """forward_to_worker raises WorkerOfflineError on remote close mid-read."""
        route_info = self._make_route_info()
        command = {"action": "restart"}

        mock_reader = MagicMock()
        mock_reader.readexactly = AsyncMock(
            side_effect=asyncio.IncompleteReadError(partial=b"abc", expected=4)
        )
        mock_writer = self._make_stream_writer()

        with patch("asyncio.open_unix_connection") as mock_open:
            mock_open.return_value = (mock_reader, mock_writer)

            with pytest.raises(WorkerOfflineError) as exc_info:
                await router.forward_to_worker(
                    machine_id="machine-123",
                    command=command,
                    route_info=route_info,
                )

        assert exc_info.value.reason == "remote_closed"

    @pytest.mark.asyncio
    async def test_forward_to_worker_envelope_structure(self, router):
        """forward_to_worker sends correctly structured envelope per D-07."""
        route_info = self._make_route_info(
            worker_pid=99999,
            socket_path="/tmp/worker_99999.sock",
        )
        command = {
            "action": "execute",
            "params": {"cmd": "ls"},
            "request_id": "req-abc-123",
        }

        response_payload = {"status": "ok"}
        response_json = json.dumps(response_payload).encode("utf-8")

        mock_reader = self._make_stream_reader(response_json)
        mock_writer = self._make_stream_writer()

        with patch("asyncio.open_unix_connection") as mock_open:
            mock_open.return_value = (mock_reader, mock_writer)

            await router.forward_to_worker(
                machine_id="machine-789",
                command=command,
                route_info=route_info,
                timeout=30.0,
            )

        # Verify envelope content
        written_bytes = b"".join(
            call[0][0] for call in mock_writer.write.call_args_list
        )
        # Strip 4-byte length prefix
        payload_bytes = written_bytes[4:]
        envelope = json.loads(payload_bytes.decode("utf-8"))

        assert envelope["target_worker_pid"] == 99999
        assert envelope["envelope"]["action"] == "execute"
        assert envelope["envelope"]["machine_id"] == "machine-789"
        assert envelope["envelope"]["params"] == {"cmd": "ls"}
        assert envelope["envelope"]["request_id"] == "req-abc-123"

    @pytest.mark.asyncio
    async def test_forward_to_worker_connect_timeout_fixed_5s(self, router):
        """forward_to_worker uses fixed 5s max connect timeout (D-11)."""
        route_info = self._make_route_info()
        command = {"action": "restart"}

        connect_timeout_used = None

        async def capture_wait_for(aw, **kwargs):
            nonlocal connect_timeout_used
            connect_timeout_used = kwargs["timeout"]
            raise TimeoutError()

        with (
            patch("asyncio.open_unix_connection"),
            patch("asyncio.wait_for", side_effect=capture_wait_for),
        ):
            try:
                await router.forward_to_worker(
                    machine_id="machine-123",
                    command=command,
                    route_info=route_info,
                    timeout=30.0,  # Overall timeout is 30s, but connect cap is 5s
                )
            except WorkerOfflineError:
                pass

        assert connect_timeout_used == 5.0

    @pytest.mark.asyncio
    async def test_forward_to_worker_wait_closed_exception_swallowed(self, router):
        """forward_to_worker swallows exception during writer.wait_closed()."""
        route_info = self._make_route_info()
        command = {"action": "restart"}

        response_json = json.dumps({"status": "ok"}).encode("utf-8")
        mock_reader = self._make_stream_reader(response_json)
        mock_writer = self._make_stream_writer()

        async def _wait_closed_raises():
            raise RuntimeError("close error")

        mock_writer.wait_closed = _wait_closed_raises

        with patch("asyncio.open_unix_connection") as mock_open:
            mock_open.return_value = (mock_reader, mock_writer)

            result = await router.forward_to_worker(
                machine_id="machine-123",
                command=command,
                route_info=route_info,
            )

        assert result == {"status": "ok"}

    # ---- D-11 self-forward defence ----

    @pytest.mark.asyncio
    async def test_forward_to_worker_self_forward_raises_worker_router_error(
        self, router
    ):
        """D-11: forward_to_worker with route_info pointing to current PID
        raises WorkerRouterError synchronously, preventing self-connect deadlock.
        """
        self_route = WorkerRouteInfo(
            worker_pid=os.getpid(),
            socket_path=f"/tmp/worker_{os.getpid()}.sock",
        )

        with pytest.raises(WorkerRouterError) as exc_info:
            await router.forward_to_worker(
                machine_id="m1",
                command={"action": "test", "params": {}},
                route_info=self_route,
            )

        assert "self-forward detected" in str(exc_info.value)
        assert f"target_pid={os.getpid()}" in str(exc_info.value)
        assert f"current_pid={os.getpid()}" in str(exc_info.value)
        # Must NOT be the more specific subclasses — defence is at base class level
        assert not isinstance(exc_info.value, RouteNotFoundError)
        assert not isinstance(exc_info.value, WorkerOfflineError)

    @pytest.mark.asyncio
    async def test_forward_to_worker_self_forward_short_circuits_before_uds_connect(
        self, router
    ):
        """D-11: self-forward guard runs before asyncio.open_unix_connection,
        so no connect attempt is made.
        """
        self_route = WorkerRouteInfo(
            worker_pid=os.getpid(),
            socket_path=f"/tmp/worker_{os.getpid()}.sock",
        )
        with patch("asyncio.open_unix_connection", new_callable=AsyncMock) as mock_conn:
            with pytest.raises(WorkerRouterError):
                await router.forward_to_worker(
                    machine_id="m1",
                    command={"action": "test", "params": {}},
                    route_info=self_route,
                )
            mock_conn.assert_not_called()

    # ---- Helpers ----

    @staticmethod
    def _make_stream_reader(payload: bytes):
        """Create a mock asyncio StreamReader that returns framed data.

        _read_framed_response first calls readexactly(4) for the length prefix,
        then readexactly(length) for the actual payload.
        """
        length_prefix = struct.pack(">I", len(payload))

        reader = MagicMock()
        reader.readexactly = AsyncMock(side_effect=[length_prefix, payload])
        return reader

    @staticmethod
    def _make_stream_writer():
        """Create a mock asyncio StreamWriter."""
        writer = MagicMock()
        writer.write = MagicMock()
        writer.drain = AsyncMock()
        writer.close = MagicMock()
        writer.wait_closed = AsyncMock()
        return writer


class TestCreateWorkerRouter:
    """Tests for factory function."""

    def test_creates_instances(self):
        """Factory returns new instances on each call (not singleton).

        Note: Singleton behavior is managed by get_worker_router() in
        config/dependencies/worker_router.py, not by create_worker_router().
        """
        from unittest.mock import MagicMock

        mock_repo = MagicMock()
        mock_cm = MagicMock()
        mock_config = MagicMock()
        router1 = WorkerRouter(
            repository=mock_repo,
            connection_manager=mock_cm,
            config=mock_config,
        )
        router2 = WorkerRouter(
            repository=mock_repo,
            connection_manager=mock_cm,
            config=mock_config,
        )

        # WorkerRouter creates new instances each time
        assert router1 is not router2
        assert isinstance(router1, WorkerRouter)
        assert isinstance(router2, WorkerRouter)
