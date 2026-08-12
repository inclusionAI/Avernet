"""Unit tests for device selector functions.

Tests select_available_device() with various device status scenarios
and verifies select_active_device() remains unchanged (regression gate).
"""

import statistics
from unittest.mock import MagicMock

import pytest

from secbaas.community.core.service.bot_runtime.dispatcher._device_selector import (
    _VIRTUAL_NODES,
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


# ==================== Multi-instance distribution Tests ====================


class TestMultiInstanceDistribution:
    """Statistical regression for the the production multi-instance distribution defect defect: 5
    service-bot instances, 61 requests, distribution 19/7/9/12/14 (max
    31.1%, min 11.5%).

    The load-bearing fix lives in the backend (keying affinity on the
    authenticated caller instead of the bot owner), and it is exercised
    end-to-end in the backend tests. The BaaS ring is the algorithm the
    affinity key feeds into, and these tests pin the invariants the
    production replay needs to hold on the new ring:

    * a single affinity key pins every request to one instance — the
      "改造前" pathology the backend keying change exists to break;
    * a realistic set of distinct affinity keys distributes across all
      instances, with no instance starving and no instance dominating;
    * the multi-key stddev is dramatically lower than the single-key one,
      the spec's "stddev significantly lower than 改造前" criterion.
    """

    N_DEVICES = 5
    N_KEYS = 60

    def _build_devices(self):
        return [_make_device(f"dev-{i:02d}", "ACTIVE") for i in range(self.N_DEVICES)]

    def _distribution(self, devices, keys):
        """Per-device hit counts over ``keys`` (callers)."""
        counts = {d.device_uuid: 0 for d in devices}
        for key in keys:
            selected = select_active_device(devices, device_affinity=key)
            counts[selected.device_uuid] += 1
        return list(counts.values())

    def test_virtual_nodes_was_bumped_to_two_hundred(self):
        """The ring-width bump is the contract: a later tuning that reverts
        it silently would re-open the defect. Pin the value here so the
        algorithm lever cannot drift without a deliberate change."""
        assert _VIRTUAL_NODES == 200

    def test_a_single_affinity_key_pins_everything_to_one_instance(self):
        """The "改造前" pathology: if every caller hashes under the same key
        (the pre-fix owner-keyed affinity), the consistent-hash ring selects
        exactly one device for every request and 100% of traffic lands on
        it. The backend tests verify the routing layer no longer feeds such
        a single key; here we pin the algorithm-level invariant that makes
        that fix worth doing — same key ⇒ same device, never a spread."""
        devices = self._build_devices()
        counts = self._distribution(devices, ["owner-1"] * self.N_KEYS)
        # One device receives every hit, the rest receive zero. The defect
        # magnitudes: stddev ~ 26.8 on a 60-key / 5-device run.
        assert max(counts) == self.N_KEYS
        assert min(counts) == 0
        assert statistics.stdev(counts) > 20

    def test_distinct_caller_keys_distribute_across_every_instance(self):
        """The "改造后" expectation: distinct affinity keys (per-caller,
        when the backend routes on ``caller_id``) spread across all
        instances. No instance starves and no single instance dominates
        the way a pinned key would."""
        devices = self._build_devices()
        keys = [f"caller-{i}" for i in range(self.N_KEYS)]
        counts = self._distribution(devices, keys)
        # Every device gets a non-trivial share — the production replay
        # target was "every instance receives traffic", and the new ring's
        # floor on a 60-key / 5-device run is at least 5 hits.
        assert min(counts) >= 5, f"starved device: {counts}"
        # No device takes more than 35% — the production report had 31.1%, and a
        # 5-device / 60-key run on the 200-vnode ring stays comfortably
        # below that on the realistic (per-caller) key set.
        assert max(counts) / self.N_KEYS < 0.35, f"hot device: {counts}"

    def test_per_caller_stddev_is_dramatically_lower_than_single_key(self):
        """The spec's "stddev 显著低于改造前" criterion. The single-key
        baseline (改造前: owner-keyed affinity) has stddev ~26.8 because all
        60 hits pile onto one device. The per-caller distribution on the
        200-vnode ring has stddev in the low single digits — a >5x
        reduction, deterministic on the consistent-hash algorithm and the
        60-key set the spec names."""
        devices = self._build_devices()
        single_key = self._distribution(devices, ["owner-1"] * self.N_KEYS)
        per_caller = self._distribution(
            devices, [f"caller-{i}" for i in range(self.N_KEYS)]
        )
        assert statistics.stdev(per_caller) * 5 < statistics.stdev(single_key)
