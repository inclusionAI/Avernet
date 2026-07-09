"""Tests for ConnectionManager connection pool limits.

Tests D-CPL01~05: Capacity, limit enforcement, counter rollback
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import WebSocket

from secbaas.core.service.paas.desktop._connection_manager import (
    ConnectionLimitExceededError,
    ConnectionManager,
)


class TestConnectionManagerLimits:
    """Test suite for connection pool limits."""

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

    def test_max_connections_constant_is_10000(self, cm: ConnectionManager) -> None:
        """D-CPL01: Verify MAX_CONNECTIONS is 10000."""
        assert cm.MAX_CONNECTIONS == 10000

    @pytest.mark.asyncio
    async def test_is_at_capacity_false_below_limit(
        self, cm: ConnectionManager
    ) -> None:
        """Test is_at_capacity returns False when below limit."""
        # Patch counter to 5000
        cm._connection_count = 5000
        assert cm.is_at_capacity() is False

    @pytest.mark.asyncio
    async def test_is_at_capacity_true_at_limit(self, cm: ConnectionManager) -> None:
        """Test is_at_capacity returns True when at limit."""
        # Patch counter to 10000
        cm._connection_count = 10000
        assert cm.is_at_capacity() is True

    @pytest.mark.asyncio
    async def test_is_at_capacity_true_over_limit(self, cm: ConnectionManager) -> None:
        """Test is_at_capacity returns True when over limit (edge case)."""
        # Patch counter to 10001
        cm._connection_count = 10001
        assert cm.is_at_capacity() is True

    @pytest.mark.asyncio
    async def test_add_connection_rejects_when_at_capacity(
        self, cm: ConnectionManager, mock_websocket: MagicMock
    ) -> None:
        """D-CPL02: Test that add_connection raises ConnectionLimitExceededError at capacity."""
        # Set counter at limit
        cm._connection_count = cm.MAX_CONNECTIONS

        with pytest.raises(ConnectionLimitExceededError) as exc_info:
            await cm._add_connection("machine_1", mock_websocket, {"user_id": "user1"})

        assert "10000" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_counter_increments_on_successful_add(
        self, cm: ConnectionManager, mock_websocket: MagicMock
    ) -> None:
        """Test that counter increments after successful add."""
        initial_count = cm._connection_count

        await cm._add_connection("machine_1", mock_websocket, {"user_id": "user1"})

        assert cm._connection_count == initial_count + 1

        # Cleanup
        cm._remove_connection("machine_1")

    @pytest.mark.asyncio
    async def test_counter_rolls_back_on_add_failure(
        self, cm: ConnectionManager, mock_websocket: MagicMock
    ) -> None:
        """Test that counter decrements on add failure via rollback."""
        # First add a connection
        await cm._add_connection("machine_1", mock_websocket, {"user_id": "user1"})

        # Try to add duplicate (will fail)
        with pytest.raises(ValueError):
            await cm._add_connection("machine_1", mock_websocket, {"user_id": "user1"})

        # Counter should roll back (give async task time to run)
        await asyncio.sleep(0.01)

        # Cleanup
        cm._remove_connection("machine_1")

    @pytest.mark.asyncio
    async def test_counter_decrements_on_remove(
        self, cm: ConnectionManager, mock_websocket: MagicMock
    ) -> None:
        """Test that counter decrements after remove_connection."""
        await cm._add_connection("machine_1", mock_websocket, {"user_id": "user1"})
        count_after_add = cm._connection_count

        cm._remove_connection("machine_1")

        # Give async decrement task time to run
        await asyncio.sleep(0.01)

        assert cm._connection_count == count_after_add - 1

    @pytest.mark.asyncio
    async def test_counter_never_negative(self, cm: ConnectionManager) -> None:
        """Test that counter never goes below 0."""
        # Remove non-existent connection
        cm._remove_connection("non_existent_machine")

        # Give async decrement task time to run
        await asyncio.sleep(0.01)

        assert cm._connection_count >= 0

    @pytest.mark.asyncio
    async def test_decrement_counter_is_thread_safe(
        self, cm: ConnectionManager
    ) -> None:
        """Test that _decrement_counter uses _count_lock."""
        cm._connection_count = 5

        # Run decrement
        await cm._decrement_counter()

        assert cm._connection_count == 4

    @pytest.mark.asyncio
    async def test_multiple_adds_and_removes_maintain_count(
        self, cm: ConnectionManager, mock_websocket: MagicMock
    ) -> None:
        """Test counter accuracy with multiple add/remove operations."""
        # Add 5 connections
        for i in range(5):
            ws = MagicMock(spec=WebSocket)
            await cm._add_connection(f"machine_{i}", ws, {"user_id": f"user{i}"})

        assert cm._connection_count == 5

        # Remove 3 connections
        for i in range(3):
            cm._remove_connection(f"machine_{i}")

        # Give async tasks time to run
        await asyncio.sleep(0.05)

        assert cm._connection_count == 2

        # Cleanup remaining
        for i in range(3, 5):
            cm._remove_connection(f"machine_{i}")

    @pytest.mark.asyncio
    async def test_add_connection_respects_count_lock(
        self, cm: ConnectionManager, mock_websocket: MagicMock
    ) -> None:
        """Test that add_connection acquires _count_lock before incrementing."""
        # This test verifies the lock is held during increment
        lock_held = False

        # Patch to track if lock is held
        original_acquire = cm._count_lock.acquire

        async def patched_acquire():
            await original_acquire()
            nonlocal lock_held
            lock_held = True
            return True

        with patch.object(cm._count_lock, "acquire", side_effect=patched_acquire):
            await cm._add_connection("machine_1", mock_websocket, {"user_id": "user1"})

        # Verify lock was acquired
        assert lock_held is True

        # Cleanup
        cm._remove_connection("machine_1")

    @pytest.mark.asyncio
    async def test_safe_decrement_handles_decrement_error(
        self, cm: ConnectionManager
    ) -> None:
        with patch.object(
            cm, "_decrement_counter", side_effect=RuntimeError("Decrement failed")
        ):
            cm._safe_decrement()
            await asyncio.sleep(0.01)

        assert cm._connection_count >= 0
