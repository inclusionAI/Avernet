"""Unit tests for device selector functions.

Tests select_available_device() with various device status scenarios
and verifies select_active_device() remains unchanged (regression gate).
"""

from unittest.mock import MagicMock

import pytest

from secbaas.community.core.service.bot_runtime.dispatcher._device_selector import (
    _resolve_virtual_nodes,
    build_ring,
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


# ==================== Virtual Node Configuration Tests ====================


class TestVirtualNodesConfig:
    """Tests for the configurable virtual-node count on the routing ring."""

    @pytest.mark.parametrize(
        "raw,expected",
        [
            (None, 200),
            ("", 200),
            ("not-an-int", 200),
            ("0", 200),
            ("-5", 200),
            ("1", 1),
            ("64", 64),
            ("500", 500),
        ],
    )
    def test_resolve_virtual_nodes_validation(self, raw, expected):
        """Invalid/empty/sub-minimum values fall back to the default."""
        assert _resolve_virtual_nodes(raw, default=200) == expected

    def test_build_ring_size_scales_with_virtual_nodes(self):
        """build_ring places each device on the ring virtual_nodes times."""
        devices = [
            _make_device("dev-a", "ACTIVE"),
            _make_device("dev-b", "ACTIVE"),
            _make_device("dev-c", "ACTIVE"),
        ]
        ring = build_ring(devices, virtual_nodes=64)
        assert len(ring) == 3 * 64
        # Ring is sorted by hash value.
        hashes = [h for h, _ in ring]
        assert hashes == sorted(hashes)

    def test_build_ring_default_uses_module_constant(self):
        """build_ring without an override uses the configured default count."""
        from secbaas.community.core.service.bot_runtime.dispatcher import (
            _device_selector as selector,
        )

        devices = [_make_device("dev-only", "ACTIVE")]
        ring = build_ring(devices)
        assert len(ring) == 1 * selector._VIRTUAL_NODES

    def test_more_virtual_nodes_reduce_skew_on_small_fleet(self):
        """A higher virtual-node count evens out load on a small device fleet.

        This is the routing-algorithm property behind the reported uneven
        multi-instance distribution: with only a few physical replicas the
        consistent-hash ring partitions unevenly unless each device owns many
        virtual nodes.
        """
        devices = [
            _make_device(f"dev-{i}", "ACTIVE") for i in range(5)
        ]
        sample_size = 6000

        def distribution(virtual_nodes: int) -> dict[str, int]:
            counts = {d.device_uuid: 0 for d in devices}
            for i in range(sample_size):
                picked = select_active_device(
                    devices,
                    device_affinity=f"session-{i}",
                    virtual_nodes=virtual_nodes,
                )
                counts[picked.device_uuid] += 1
            return counts

        few = distribution(virtual_nodes=1)
        many = distribution(virtual_nodes=200)

        def spread(counts: dict[str, int]) -> int:
            return max(counts.values()) - min(counts.values())

        # The all-replicas-identical degenerate ring (1 vnode per device) is
        # far more skewed than the 200-vnode ring over the same keys.
        assert spread(few) > spread(many)
        # And the 200-vnode ring stays within a reasonable band of the mean.
        mean = sample_size / len(devices)
        assert max(many.values()) <= mean * 1.20
        assert min(many.values()) >= mean * 0.80
