"""Regression: GET /api/devices must not 500 on desktop reconciliation statuses.

The desktop bot health-check scan writes ``OFFLINE`` (BaaS ALL_OFFLINE /
pending-transition timeout) and ``RELEASING`` (BaaS DESTROYING) into device
binding rows. ``record_to_response`` parses ``record.status`` with the
``DeviceBindingStatus`` enum, so any status the scan persists must be a member
or the whole ``list_devices`` endpoint blows up for the owning user.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from agentclaw.community.adapters.http.devices.converter import record_to_response
from agentclaw.community.core.devices.models import DeviceBindingStatus
from agentclaw.community.adapters.http.devices.schemas import (
    DeviceBindingStatus as HttpDeviceBindingStatus,
)
from agentclaw.community.core.devices.repository.record import DeviceBindingRecord


def _record(status: str) -> DeviceBindingRecord:
    return DeviceBindingRecord(
        id=101,
        entity_id="448524",
        entity_type="staff",
        device_id="device_001",
        device_provider="desktop",
        env="prod",
        device_props={},
        status=status,
        apply_reason=None,
        applied_by="staff_448524",
        release_reason=None,
        released_by=None,
        released_at=None,
        last_alive_at=datetime(2026, 11, 27, 0, 46, 22),
        gmt_create=None,
        gmt_modified=None,
    )


@pytest.mark.parametrize(
    "status",
    [member.value for member in DeviceBindingStatus],
)
def test_record_to_response_accepts_every_binding_status(status: str) -> None:
    response = record_to_response(_record(status))

    assert response.status == status


def test_record_to_response_accepts_persisted_offline() -> None:
    # Exact signature of the CDO error: 'OFFLINE' is not a valid
    # DeviceBindingStatus — previously raised by the enum parse below.
    response = record_to_response(_record("OFFLINE"))

    assert response.status == HttpDeviceBindingStatus.OFFLINE


def test_record_to_response_accepts_persisted_releasing() -> None:
    response = record_to_response(_record("RELEASING"))

    assert response.status == HttpDeviceBindingStatus.RELEASING


def test_unknown_status_still_rejected() -> None:
    # Guard that the enum stays strict: typos or future BaaS values should
    # fail loudly in tests rather than silently pass through the API.
    with pytest.raises(ValueError):
        record_to_response(_record("VANISHED"))
