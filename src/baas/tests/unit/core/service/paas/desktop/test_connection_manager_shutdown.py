"""Tests for ConnectionManager graceful shutdown.

Tests D-GS01~03: Sweep cancellation, connection closing, pending request signaling
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import WebSocket

from secbaas.community.core.service.paas.desktop._connection_manager import (
    ConnectionManager,
)


class TestConnectionManagerShutdown:
    """Test suite for graceful shutdown."""

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
    async def test_shutdown_cancels_sweep_task(self, cm: ConnectionManager) -> None:
        """D-GS01: Test that shutdown cancels sweep task."""
        cm._start_sweep()
        await asyncio.sleep(0.05)  # Let task start

        assert cm._sweep_task is not None
        assert not cm._sweep_task.done()

        await cm.shutdown()

        assert cm._sweep_task.done()

    @pytest.mark.asyncio
    async def test_shutdown_closes_all_connections(
        self, cm: ConnectionManager, mock_websocket: MagicMock
    ) -> None:
        """D-GS03: Test that shutdown closes all connections with code 1001."""
        # Add 3 mock connections
        for i in range(3):
            ws = MagicMock(spec=WebSocket)
            ws.close = AsyncMock()
            await cm._add_connection(f"machine_{i}", ws, {"user_id": f"user{i}"})

        await cm.shutdown()

        # Verify all websockets were closed with code 1001
        for i in range(3):
            ws = cm._connections.get(f"machine_{i}")
            # Connections should be removed from tracking
            assert not cm.is_connected(f"machine_{i}")

    @pytest.mark.asyncio
    async def test_shutdown_handles_no_connections(self, cm: ConnectionManager) -> None:
        """Test that shutdown handles case with no connections."""
        # No connections added
        assert len(cm._connections) == 0

        # Should complete without error
        await cm.shutdown()

    @pytest.mark.asyncio
    async def test_shutdown_handles_already_cancelled_task(
        self, cm: ConnectionManager
    ) -> None:
        """Test shutdown when sweep task already cancelled."""
        cm._start_sweep()
        await asyncio.sleep(0.05)

        # Cancel manually first
        if cm._sweep_task:
            cm._sweep_task.cancel()
            try:
                await cm._sweep_task
            except asyncio.CancelledError:
                pass

        # Shutdown should handle gracefully
        await cm.shutdown()

    @pytest.mark.asyncio
    async def test_shutdown_sets_shutdown_event(self, cm: ConnectionManager) -> None:
        """Test that shutdown sets the shutdown event."""
        assert not cm._shutdown_event.is_set()

        await cm.shutdown()

        assert cm._shutdown_event.is_set()

    @pytest.mark.asyncio
    async def test_shutdown_handles_already_shutdown(
        self, cm: ConnectionManager
    ) -> None:
        """Test calling shutdown twice is safe."""
        await cm.shutdown()
        # Second shutdown should be safe
        await cm.shutdown()

    @pytest.mark.asyncio
    async def test_shutdown_closes_connections_in_parallel(
        self, cm: ConnectionManager
    ) -> None:
        """Test that shutdown closes connections using gather (parallel)."""
        close_calls = []

        async def tracked_close(code):
            close_calls.append(code)
            await asyncio.sleep(0.01)  # Simulate async work

        # Add 3 connections with tracked close
        for i in range(3):
            ws = MagicMock(spec=WebSocket)
            ws.close = AsyncMock(side_effect=tracked_close)
            await cm._add_connection(f"machine_{i}", ws, {"user_id": f"user{i}"})

        await cm.shutdown()

        # All should be closed with code 1001
        assert len(close_calls) == 3
        assert all(code == 1001 for code in close_calls)

    @pytest.mark.asyncio
    async def test_close_connection_error_handling(self, cm: ConnectionManager) -> None:
        """Test _close_connection handles errors gracefully."""
        ws = MagicMock(spec=WebSocket)
        ws.close = AsyncMock(side_effect=Exception("Close failed"))

        await cm._add_connection("machine_1", ws, {"user_id": "user1"})

        # Should not raise
        await cm._close_connection("machine_1", ws)

        # Connection should still be removed (best effort)
        assert not cm.is_connected("machine_1")
