"""Unit tests for device selector functions.

Tests select_available_device() with various device status scenarios
and verifies select_active_device() remains unchanged (regression gate).
"""

from unittest.mock import MagicMock

import pytest

from secbaas.community.core.service.bot_runtime.dispatcher._device_selector import (
    select_active_device,
    select_available_device,
)

# ==================== Helpers ====================


def _make_device(device_uuid: str, status: str) -> MagicMock:
    """Create a mock DeviceRecord with the given UUID and status."""
    device = MagicMock()
    device.device_uuid = device_uuid
    device.status = status
    return device


# ==================== select_available_device Tests ====================


class TestSelectAvailableDevice:
    """Tests for select_available_device() with the status whitelist
    (ACTIVE, PENDING, UPDATING, OFFLINE) and excluded states (RELEASED, FAILED, STOPPED).
    """

    def test_returns_active_device(self):
        """When devices list contains an ACTIVE device, it is selected."""
        devices = [
            _make_device("dev-001", "ACTIVE"),
        ]
        result = select_available_device(devices)
        assert result is not None
        assert result.device_uuid == "dev-001"
        assert result.status == "ACTIVE"

    def test_returns_pending_device(self):
        """When devices list contains only a PENDING device, it is selected."""
        devices = [
            _make_device("dev-002", "PENDING"),
        ]
        result = select_available_device(devices)
        assert result is not None
        assert result.device_uuid == "dev-002"
        assert result.status == "PENDING"

    def test_returns_updating_device(self):
        """When devices list contains only an UPDATING device, it is selected."""
        devices = [
            _make_device("dev-003", "UPDATING"),
        ]
        result = select_available_device(devices)
        assert result is not None
        assert result.device_uuid == "dev-003"
        assert result.status == "UPDATING"

    def test_returns_offline_device(self):
        """When devices list contains only an OFFLINE device, it is selected.

        OFFLINE represents a transient state where the device is reachable
        but its daemon/service is temporarily unavailable (e.g., during
        mng daemon reconnection in the LOCAL platform startup window).
        """
        devices = [
            _make_device("dev-offline", "OFFLINE"),
        ]
        result = select_available_device(devices)
        assert result is not None
        assert result.device_uuid == "dev-offline"
        assert result.status == "OFFLINE"

    def test_excludes_failed_released_stopped(self):
        """When all devices are in excluded states, returns None."""
        devices = [
            _make_device("dev-004", "RELEASED"),
            _make_device("dev-005", "FAILED"),
            _make_device("dev-006", "STOPPED"),
        ]
        result = select_available_device(devices)
        assert result is None

    def test_mixed_statuses_returns_available(self):
        """With ACTIVE and FAILED devices, returns the ACTIVE one."""
        devices = [
            _make_device("dev-007", "FAILED"),
            _make_device("dev-008", "ACTIVE"),
        ]
        result = select_available_device(devices)
        assert result is not None
        assert result.device_uuid == "dev-008"
        assert result.status == "ACTIVE"

    def test_affinity_sticky_selection(self):
        """Same affinity key returns the same device across calls."""
        devices = [
            _make_device("dev-a", "ACTIVE"),
            _make_device("dev-b", "ACTIVE"),
            _make_device("dev-c", "ACTIVE"),
        ]
        # Call multiple times with same affinity key; should return same device
        first = select_available_device(devices, device_affinity="user-123")
        for _ in range(10):
            result = select_available_device(devices, device_affinity="user-123")
            assert result is not None
            assert result.device_uuid == first.device_uuid

    def test_empty_list_returns_none(self):
        """Empty devices list returns None."""
        result = select_available_device([])
        assert result is None

    def test_random_no_affinity(self):
        """Without device_affinity, uses random selection (returns a device)."""
        devices = [
            _make_device("dev-x", "ACTIVE"),
            _make_device("dev-y", "PENDING"),
        ]
        result = select_available_device(devices, device_affinity=None)
        assert result is not None
        assert result.device_uuid in ("dev-x", "dev-y")


# ==================== Regression Tests ====================


class TestSelectActiveDeviceRegression:
    """Verify select_active_device() behavior is completely unchanged."""

    def test_select_active_device_still_actives_only(self):
        """select_active_device() still filters to ACTIVE only (PENDING excluded)."""
        devices = [
            _make_device("dev-active", "ACTIVE"),
            _make_device("dev-pending", "PENDING"),
            _make_device("dev-updating", "UPDATING"),
        ]
        result = select_active_device(devices)
        assert result is not None
        assert result.device_uuid == "dev-active"
        assert result.status == "ACTIVE"

    def test_select_active_device_returns_none_when_no_active(self):
        """select_active_device() returns None when only PENDING devices exist."""
        devices = [
            _make_device("dev-pending", "PENDING"),
            _make_device("dev-updating", "UPDATING"),
        ]
        result = select_active_device(devices)
        assert result is None
