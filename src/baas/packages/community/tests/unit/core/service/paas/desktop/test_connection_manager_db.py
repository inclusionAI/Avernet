"""Tests for ConnectionManager database integration.

Tests D-DB01~06: Repository calls, error handling matrix
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest
from fastapi import WebSocket

from secbaas.core.repository.local_user_machine import (
    LocalUserMachineRecord,
    LocalUserMachineRepository,
)
from secbaas.core.service.paas.desktop._connection_manager import ConnectionManager


class TestConnectionManagerDb:
    """Test suite for database integration."""

    @pytest.fixture
    def cm(self) -> ConnectionManager:
        """Fixture providing fresh ConnectionManager instance with default mock repo."""
        return ConnectionManager(repository=MagicMock())

    @pytest.fixture
    def cm_with_repo(self, mock_repository: MagicMock) -> ConnectionManager:
        """Fixture providing ConnectionManager with the specific mock_repository."""
        return ConnectionManager(repository=mock_repository)

    @pytest.fixture
    def mock_repository(self) -> MagicMock:
        """Fixture providing mocked repository conforming to Protocol."""
        repo = MagicMock(spec=LocalUserMachineRepository)
        repo.update_heartbeat = MagicMock()
        repo.update_status = MagicMock()
        repo.update_instance = MagicMock()
        return repo

    @pytest.fixture
    def mock_websocket(self) -> MagicMock:
        """Fixture providing mocked WebSocket."""
        ws = MagicMock(spec=WebSocket)
        return ws

    def test_initialize_sets_repository_and_env(
        self, cm_with_repo: ConnectionManager, mock_repository: MagicMock
    ) -> None:
        """Test that initialize sets repository, env, and instance_id."""
        cm_with_repo.initialize(env="test_env", instance_id="test_instance")

        assert cm_with_repo._repository is mock_repository
        assert cm_with_repo._env == "test_env"
        assert cm_with_repo._instance_id == "test_instance"

    @pytest.mark.asyncio
    async def test_on_connect_updates_instance_and_status(
        self, cm_with_repo: ConnectionManager, mock_repository: MagicMock
    ) -> None:
        """D-DB03: Test _on_connect updates instance and status=ONLINE."""
        cm_with_repo.initialize(env="test_env", instance_id="test_instance")

        cm_with_repo._on_connect("machine_1", "user_1")

        # Verify repository calls
        mock_repository.update_instance.assert_called_once_with(
            "machine_1", "test_env", "test_instance"
        )
        mock_repository.update_status.assert_called_once_with(
            "machine_1", "test_env", "ONLINE"
        )

    @pytest.mark.asyncio
    async def test_on_connect_propagates_errors(
        self, cm_with_repo: ConnectionManager, mock_repository: MagicMock
    ) -> None:
        """D-DB03: Test _on_connect propagates errors to reject connection."""
        cm_with_repo.initialize(env="test_env", instance_id="test_instance")

        # Make update_instance raise
        mock_repository.update_instance.side_effect = Exception("DB error")

        with pytest.raises(Exception) as exc_info:
            cm_with_repo._on_connect("machine_1", "user_1")

        assert "DB error" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_update_heartbeat_calls_repository(
        self,
        cm_with_repo: ConnectionManager,
        mock_repository: MagicMock,
        mock_websocket: MagicMock,
    ) -> None:
        """D-DB04: Test _update_heartbeat calls repository."""
        cm_with_repo.initialize(env="test_env", instance_id="test_instance")
        await cm_with_repo._add_connection(
            "machine_1", mock_websocket, {"user_id": "user1"}
        )

        # Clear calls from _add_connection
        mock_repository.reset_mock()

        await cm_with_repo._update_heartbeat("machine_1")

        # Verify repository call
        mock_repository.update_heartbeat.assert_called_once()
        call_args = mock_repository.update_heartbeat.call_args
        assert call_args[0][0] == "machine_1"  # machine_id
        assert call_args[0][1] == "test_env"  # env
        assert isinstance(call_args[0][2], datetime)  # timestamp

        # Cleanup
        cm_with_repo._remove_connection("machine_1")

    @pytest.mark.asyncio
    async def test_update_heartbeat_logs_error_continues(
        self,
        cm_with_repo: ConnectionManager,
        mock_repository: MagicMock,
        mock_websocket: MagicMock,
    ) -> None:
        """D-DB04: Test _update_heartbeat logs error and continues."""
        cm_with_repo.initialize(env="test_env", instance_id="test_instance")
        await cm_with_repo._add_connection(
            "machine_1", mock_websocket, {"user_id": "user1"}
        )

        # Make repository raise
        mock_repository.update_heartbeat.side_effect = Exception("DB error")

        # Should NOT raise
        await cm_with_repo._update_heartbeat("machine_1")

        # Connection should still exist
        assert cm_with_repo.is_connected("machine_1")

        # Cleanup
        cm_with_repo._remove_connection("machine_1")

    @pytest.mark.asyncio
    async def test_on_disconnect_updates_status_offline(
        self, cm_with_repo: ConnectionManager, mock_repository: MagicMock
    ) -> None:
        """D-DB05: Test _on_disconnect updates status to OFFLINE."""
        cm_with_repo.initialize(env="test_env", instance_id="test_instance")

        cm_with_repo._on_disconnect("machine_1")

        mock_repository.update_status.assert_called_once_with(
            "machine_1", "test_env", "OFFLINE"
        )

    @pytest.mark.asyncio
    async def test_on_disconnect_clears_instance(
        self, cm_with_repo: ConnectionManager, mock_repository: MagicMock
    ) -> None:
        """D-DB05: Test _on_disconnect clears connected_server_instance."""
        cm_with_repo.initialize(env="test_env", instance_id="test_instance")

        cm_with_repo._on_disconnect("machine_1")

        mock_repository.update_instance.assert_called_with("machine_1", "test_env", "")

    @pytest.mark.asyncio
    async def test_on_disconnect_logs_warning_on_error(
        self, cm_with_repo: ConnectionManager, mock_repository: MagicMock
    ) -> None:
        """D-DB05: Test _on_disconnect logs warning and doesn't propagate."""
        cm_with_repo.initialize(env="test_env", instance_id="test_instance")

        # Make update_status raise
        mock_repository.update_status.side_effect = Exception("DB error")

        # Should NOT raise
        cm_with_repo._on_disconnect("machine_1")

    def test_initialize_auto_detects_instance_id(
        self, cm_with_repo: ConnectionManager, mock_repository: MagicMock
    ) -> None:
        """Test that initialize auto-detects instance_id if not provided."""
        with patch(
            "secbaas.core.service.paas.desktop._connection_manager.get_instance_id",
            return_value="auto_detected",
        ):
            cm_with_repo.initialize(env="test_env")
            assert cm_with_repo._instance_id == "auto_detected"

    @pytest.mark.asyncio
    async def test_update_heartbeat_skips_db_if_no_repository(
        self, cm: ConnectionManager, mock_websocket: MagicMock
    ) -> None:
        """Test _update_heartbeat skips DB if repository not initialized."""
        await cm._add_connection("machine_1", mock_websocket, {"user_id": "user1"})

        # Should complete without error (no repository)
        await cm._update_heartbeat("machine_1")

        # Cleanup
        cm._remove_connection("machine_1")

    def test_on_disconnect_skips_if_no_repository(self, cm: ConnectionManager) -> None:
        """Test _on_disconnect skips if repository not initialized."""
        # Should complete without error
        cm._on_disconnect("machine_1")

    def test_on_connect_raises_if_repository_none(self) -> None:
        """Test _on_connect raises ConnectionError if repository is None.

        Repository must be passed via constructor. This tests the case
        where a subclass or alternative constructor creates CM with
        repository=None.
        """
        cm_no_repo = ConnectionManager.__new__(ConnectionManager)
        cm_no_repo._repository = None  # type: ignore[attr-defined]

        with pytest.raises(ConnectionError) as exc_info:
            cm_no_repo._on_connect("machine_1", "user_1")

        assert "not initialized" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_on_disconnect_idempotent_already_offline(
        self, cm_with_repo: ConnectionManager, mock_repository: MagicMock
    ) -> None:
        """Test _on_disconnect skips updates when record already OFFLINE."""
        cm_with_repo.initialize(env="test_env", instance_id="test_instance")

        offline_record = MagicMock(spec=LocalUserMachineRecord)
        offline_record.status = "OFFLINE"
        mock_repository.get_by_machine_id.return_value = offline_record

        cm_with_repo._on_disconnect("machine_1")

        mock_repository.get_by_machine_id.assert_called_once_with(
            "machine_1", "test_env"
        )
        mock_repository.update_status.assert_not_called()
        mock_repository.update_instance.assert_not_called()

    @pytest.mark.asyncio
    async def test_on_disconnect_idempotent_no_record(
        self, cm_with_repo: ConnectionManager, mock_repository: MagicMock
    ) -> None:
        """Test _on_disconnect proceeds normally when no existing record."""
        cm_with_repo.initialize(env="test_env", instance_id="test_instance")

        mock_repository.get_by_machine_id.return_value = None

        cm_with_repo._on_disconnect("machine_1")

        mock_repository.update_status.assert_called_once_with(
            "machine_1", "test_env", "OFFLINE"
        )
        mock_repository.update_instance.assert_called_once_with(
            "machine_1", "test_env", ""
        )

    @pytest.mark.asyncio
    async def test_update_heartbeat_instance_drift_auto_fix(
        self,
        cm_with_repo: ConnectionManager,
        mock_repository: MagicMock,
        mock_websocket: MagicMock,
    ) -> None:
        """Test _update_heartbeat auto-fixes instance mismatch."""
        cm_with_repo.initialize(env="test_env", instance_id="test_instance")
        await cm_with_repo._add_connection(
            "machine_1", mock_websocket, {"user_id": "user1"}
        )

        drift_record = MagicMock(spec=LocalUserMachineRecord)
        drift_record.connected_server_instance = "other_instance"
        mock_repository.get_by_machine_id.return_value = drift_record

        await cm_with_repo._update_heartbeat("machine_1")

        mock_repository.update_instance.assert_called_with(
            "machine_1", "test_env", "test_instance"
        )
        mock_repository.update_status.assert_called_with(
            "machine_1", "test_env", "ONLINE"
        )

        cm_with_repo._remove_connection("machine_1")

    @pytest.mark.asyncio
    async def test_update_heartbeat_instance_drift_error_handling(
        self,
        cm_with_repo: ConnectionManager,
        mock_repository: MagicMock,
        mock_websocket: MagicMock,
    ) -> None:
        """Test _update_heartbeat handles drift check error gracefully."""
        cm_with_repo.initialize(env="test_env", instance_id="test_instance")
        await cm_with_repo._add_connection(
            "machine_1", mock_websocket, {"user_id": "user1"}
        )

        mock_repository.get_by_machine_id.side_effect = Exception("Drift check error")

        await cm_with_repo._update_heartbeat("machine_1")

        assert cm_with_repo.is_connected("machine_1")

        cm_with_repo._remove_connection("machine_1")
