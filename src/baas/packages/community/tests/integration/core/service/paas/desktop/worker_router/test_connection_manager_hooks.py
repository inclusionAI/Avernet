"""Tests for ConnectionManager route_info hook integration.

D-09: Heartbeat refreshes route_info.
D-10: on_disconnect clears only when PID matches.
D-12: on_connect continues on write failure.
"""

import os
from unittest.mock import MagicMock

import pytest

pytestmark = pytest.mark.integration

from secbaas.core.service.paas.desktop._connection_manager import ConnectionManager
from secbaas.core.service.paas.desktop.worker_router import UDSConfig


class TestConnectionManagerRouteInfoHooks:
    """Tests for route_info integration in ConnectionManager."""

    @pytest.fixture
    def manager(self):
        """Create ConnectionManager with mocked repository."""
        cm = ConnectionManager(repository=MagicMock())
        cm._repository = MagicMock()
        cm._env = "test"
        cm._instance_id = "127.0.0.1"
        return cm

    def test_get_current_route_info_format(self, manager):
        """Verify route_info format matches D-07 specification."""
        route_info = manager._get_current_route_info()

        assert "worker_pid" in route_info
        assert "socket_path" in route_info
        assert route_info["worker_pid"] == os.getpid()
        assert f"worker_{os.getpid()}.sock" in route_info["socket_path"]

    def test_get_current_route_info_respects_uds_config_socket_dir(self, manager):
        """D-09: custom UDSConfig.socket_dir flows through to socket_path."""
        custom = UDSConfig(socket_dir="/tmp/custom_baas_workers")
        manager._uds_config = custom  # direct assignment to verify wiring
        route_info = manager._get_current_route_info()
        assert (
            route_info["socket_path"]
            == f"/tmp/custom_baas_workers/worker_{os.getpid()}.sock"
        )

    def test_initialize_accepts_uds_config_parameter(self, manager):
        """D-09: initialize() accepts uds_config and stores it."""
        custom = UDSConfig(socket_dir="/tmp/another_dir")
        manager.initialize(
            env="test",
            uds_config=custom,
        )
        assert manager._uds_config is custom
        assert (
            manager._get_current_route_info()["socket_path"]
            == f"/tmp/another_dir/worker_{os.getpid()}.sock"
        )

    def test_on_connect_writes_route_info(self, manager):
        """D-07: on_connect writes route_info to database."""
        manager._on_connect("machine-123", "user-456")

        # Verify update_route_info called
        manager._repository.update_route_info.assert_called_once()
        call_args = manager._repository.update_route_info.call_args
        machine_id, env, route_info = call_args[0]

        assert machine_id == "machine-123"
        assert env == "test"
        assert route_info["worker_pid"] == os.getpid()

    def test_on_connect_continues_on_route_info_failure(self, manager):
        """D-12: on_connect accepts connection even if route_info write fails."""
        # Make update_route_info fail but others succeed
        manager._repository.update_route_info.side_effect = Exception("DB error")

        # Should not raise even though route_info write fails
        manager._on_connect("machine-123", "user-456")

        # Verify status updates were attempted
        manager._repository.update_instance.assert_called_once()
        manager._repository.update_status.assert_called_once()

    def test_on_disconnect_clears_route_info_when_pid_matches(self, manager):
        """D-10: on_disconnect clears only if PID matches."""
        current_pid = os.getpid()

        # Setup: route_info exists with current PID
        manager._repository.get_route_info.return_value = {
            "worker_pid": current_pid,
            "socket_path": f"/tmp/worker_{current_pid}.sock",
        }
        manager._repository.get_by_machine_id.return_value = MagicMock(status="ONLINE")

        manager._on_disconnect("machine-123")

        # Verify clear_route_info called
        manager._repository.clear_route_info.assert_called_once_with(
            "machine-123", "test"
        )

    def test_on_disconnect_skips_clear_when_pid_mismatch(self, manager):
        """D-10: on_disconnect skips clear if another worker owns route_info."""
        # Setup: route_info exists with different PID
        manager._repository.get_route_info.return_value = {
            "worker_pid": 99999,  # Different PID
            "socket_path": "/tmp/worker_99999.sock",
        }
        manager._repository.get_by_machine_id.return_value = MagicMock(status="ONLINE")

        manager._on_disconnect("machine-123")

        # Verify clear_route_info NOT called
        manager._repository.clear_route_info.assert_not_called()

    @pytest.mark.asyncio
    async def test_update_heartbeat_refreshes_route_info(self, manager):
        """D-09: Heartbeat refreshes route_info."""
        # Setup connection
        manager._connections = {"machine-123": MagicMock()}
        manager._metadata = {"machine-123": {"last_heartbeat": MagicMock()}}

        # Mock get_by_machine_id for instance check
        manager._repository.get_by_machine_id.return_value = MagicMock(
            connected_server_instance="127.0.0.1"
        )
        # Stored route_info is None on first heartbeat -> must trigger REFRESH path
        manager._repository.get_route_info.return_value = None

        await manager._update_heartbeat("machine-123")

        # Verify update_route_info called
        manager._repository.update_route_info.assert_called()

    @pytest.mark.asyncio
    async def test_update_heartbeat_skips_route_info_update_when_unchanged(
        self, manager
    ):
        """D-13: When stored route_info equals current, update_route_info is NOT called."""
        manager._connections = {"machine-123": MagicMock()}
        manager._metadata = {"machine-123": {"last_heartbeat": MagicMock()}}

        manager._repository.get_by_machine_id.return_value = MagicMock(
            connected_server_instance="127.0.0.1"
        )

        current = manager._get_current_route_info()
        manager._repository.get_route_info.return_value = dict(current)

        await manager._update_heartbeat("machine-123")

        manager._repository.get_route_info.assert_called_once_with(
            "machine-123", "test"
        )
        manager._repository.update_route_info.assert_not_called()
        manager._repository.update_heartbeat.assert_called_once()

    @pytest.mark.asyncio
    async def test_update_heartbeat_refreshes_route_info_when_stored_pid_differs(
        self, manager
    ):
        """D-13: When stored worker_pid differs, update_route_info IS called."""
        manager._connections = {"machine-123": MagicMock()}
        manager._metadata = {"machine-123": {"last_heartbeat": MagicMock()}}

        manager._repository.get_by_machine_id.return_value = MagicMock(
            connected_server_instance="127.0.0.1"
        )
        manager._repository.get_route_info.return_value = {
            "worker_pid": 99999,
            "socket_path": f"{os.path.expanduser('~')}/secbaas_workers/worker_99999.sock",
        }

        await manager._update_heartbeat("machine-123")

        manager._repository.update_route_info.assert_called_once()
        refreshed = manager._repository.update_route_info.call_args[0][2]
        assert refreshed["worker_pid"] == os.getpid()
        assert refreshed["worker_pid"] != 99999

    @pytest.mark.asyncio
    async def test_update_heartbeat_refreshes_route_info_when_stored_is_none(
        self, manager
    ):
        """D-13: When no stored route_info exists, update_route_info IS called."""
        manager._connections = {"machine-123": MagicMock()}
        manager._metadata = {"machine-123": {"last_heartbeat": MagicMock()}}

        manager._repository.get_by_machine_id.return_value = MagicMock(
            connected_server_instance="127.0.0.1"
        )
        manager._repository.get_route_info.return_value = None

        await manager._update_heartbeat("machine-123")

        manager._repository.update_route_info.assert_called_once()
