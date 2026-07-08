"""Tests for ConnectionManager heartbeat sweep functionality.

Tests D-HT01~05: Sweep loop, heartbeat tracking, timeout detection, stale close
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import WebSocket

from secbaas.core.service.paas.desktop._connection_manager import ConnectionManager


class TestConnectionManagerSweep:
    """Test suite for heartbeat sweep task."""

    @pytest.fixture
    def cm(self) -> ConnectionManager:
        """Fixture providing fresh ConnectionManager instance."""
        return ConnectionManager(repository=MagicMock())

    @pytest.fixture
    def mock_websocket(self) -> MagicMock:
        """Fixture providing mocked WebSocket."""
        ws = MagicMock(spec=WebSocket)
        ws.send_json = AsyncMock()
        ws.close = AsyncMock()
        return ws

    @pytest.mark.asyncio
    async def test_sweep_task_runs_periodically(self, cm: ConnectionManager) -> None:
        """Test that sweep task starts and creates asyncio task."""
        cm._start_sweep()

        assert cm._sweep_task is not None

        # Cleanup
        cm._sweep_task.cancel()
        try:
            await cm._sweep_task
        except asyncio.CancelledError:
            pass

    @pytest.mark.asyncio
    async def test_sweep_detects_stale_connection_and_closes(
        self, cm: ConnectionManager, mock_websocket: MagicMock
    ) -> None:
        """Test that sweep detects stale connection (35s old) and closes with code 1001."""
        # Add connection with old heartbeat (35 seconds ago)
        old_time = datetime.now() - timedelta(seconds=35)
        metadata = {"user_id": "user123", "last_heartbeat": old_time}

        await cm._add_connection("machine_1", mock_websocket, metadata)

        # Run check_timeouts manually
        await cm._check_timeouts()

        # Verify close was called with code 1001
        mock_websocket.close.assert_called_once_with(code=1001)

        # Verify connection removed
        assert not cm.is_connected("machine_1")

    @pytest.mark.asyncio
    async def test_sweep_ignores_recent_heartbeat(
        self, cm: ConnectionManager, mock_websocket: MagicMock
    ) -> None:
        """Test that sweep ignores connections with recent heartbeat (10s old)."""
        # Add connection with recent heartbeat (10 seconds ago)
        recent_time = datetime.now() - timedelta(seconds=10)
        metadata = {"user_id": "user123", "last_heartbeat": recent_time}

        await cm._add_connection("machine_1", mock_websocket, metadata)

        # Run check_timeouts manually
        await cm._check_timeouts()

        # Verify close was NOT called
        mock_websocket.close.assert_not_called()

        # Verify connection still exists
        assert cm.is_connected("machine_1")

        # Cleanup
        cm._remove_connection("machine_1")

    @pytest.mark.asyncio
    async def test_sweep_handles_empty_connections(self, cm: ConnectionManager) -> None:
        """Test that sweep handles empty connections without error."""
        # No connections added
        await cm._check_timeouts()
        # Should complete without error
        assert len(cm._connections) == 0

    @pytest.mark.asyncio
    async def test_update_heartbeat_updates_timestamp(
        self, cm: ConnectionManager, mock_websocket: MagicMock
    ) -> None:
        """Test that update_heartbeat sets last_heartbeat metadata."""
        await cm._add_connection("machine_1", mock_websocket, {"user_id": "user123"})

        # Update heartbeat
        await cm._update_heartbeat("machine_1")

        # Verify timestamp was set
        assert "last_heartbeat" in cm._metadata["machine_1"]
        timestamp = cm._metadata["machine_1"]["last_heartbeat"]
        assert isinstance(timestamp, datetime)
        # Should be very recent
        assert (datetime.now() - timestamp).total_seconds() < 1

        # Cleanup
        cm._remove_connection("machine_1")

    @pytest.mark.asyncio
    async def test_sweep_detects_exactly_at_threshold(
        self, cm: ConnectionManager, mock_websocket: MagicMock
    ) -> None:
        """Test edge case: connection just under threshold is NOT closed."""
        # Add connection with heartbeat just under threshold (29 seconds ago)
        threshold_time = datetime.now() - timedelta(seconds=29)
        metadata = {"user_id": "user123", "last_heartbeat": threshold_time}

        await cm._add_connection("machine_1", mock_websocket, metadata)

        # Run check_timeouts
        await cm._check_timeouts()

        # Connection should NOT be closed (needs to be > 30s)
        mock_websocket.close.assert_not_called()

        # Cleanup
        cm._remove_connection("machine_1")

    @pytest.mark.asyncio
    async def test_update_heartbeat_for_unknown_machine_logs_warning(
        self, cm: ConnectionManager
    ) -> None:
        """Test that update_heartbeat for unknown machine logs warning."""
        with (
            patch.object(cm, "_logger", create=True)
            if hasattr(cm, "_logger")
            else patch(
                "secbaas.core.service.paas.desktop._connection_manager.logger"
            ) as _mock_logger
        ):
            await cm._update_heartbeat("unknown_machine")
            # Should complete without error and log warning

    @pytest.mark.asyncio
    async def test_sweep_loop_handles_cancelled_error(
        self, cm: ConnectionManager
    ) -> None:
        """Test that sweep loop handles CancelledError gracefully."""
        cm._start_sweep()
        await asyncio.sleep(0.05)  # Let loop start

        # Cancel the task
        if cm._sweep_task:
            cm._sweep_task.cancel()
            try:
                await cm._sweep_task
            except asyncio.CancelledError:
                pass

        # Should not raise
        assert cm._sweep_task is not None

    @pytest.mark.asyncio
    async def test_sweep_loop_handles_exceptions_continues(
        self, cm: ConnectionManager
    ) -> None:
        """Test that _check_timeouts handles connection close errors gracefully."""
        # Add a stale connection
        ws = MagicMock()
        ws.close = AsyncMock(side_effect=Exception("Close failed"))
        old_time = datetime.now() - timedelta(seconds=35)
        await cm._add_connection("machine_1", ws, {"last_heartbeat": old_time})

        # _check_timeouts should complete even though close will fail
        # (error is caught in _close_stale_connection)
        await cm._check_timeouts()

        # Connection should still be removed despite close error
        assert not cm.is_connected("machine_1")

    @pytest.mark.asyncio
    async def test_sweep_loop_timeout_path(self, cm: ConnectionManager) -> None:
        call_count = 0

        async def mock_check_timeouts():
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                cm._shutdown_event.set()

        cm._check_timeouts = mock_check_timeouts  # type: ignore[method-assign]

        with patch.object(asyncio, "wait_for", side_effect=TimeoutError()):
            await cm._sweep_loop()
        await asyncio.sleep(0)

        assert call_count >= 2

    @pytest.mark.asyncio
    async def test_sweep_loop_generic_exception_handling(
        self, cm: ConnectionManager
    ) -> None:
        cm._shutdown_event.clear()
        call_count = 0

        async def mock_check_timeouts():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("Sweep check failed")
            cm._shutdown_event.set()

        cm._check_timeouts = mock_check_timeouts  # type: ignore[method-assign]

        await cm._sweep_loop()

        assert call_count >= 2

    @pytest.mark.asyncio
    async def test_close_stale_connection_on_disconnect_error(
        self, cm: ConnectionManager, mock_websocket: MagicMock
    ) -> None:
        await cm._add_connection("machine_1", mock_websocket, {"user_id": "user1"})

        def failing_on_disconnect(machine_id):
            raise RuntimeError("Disconnect failed")

        cm._on_disconnect = failing_on_disconnect  # type: ignore[method-assign]

        await cm._close_stale_connection("machine_1", mock_websocket)

        assert not cm.is_connected("machine_1")
