from __future__ import annotations

import pytest
from pydantic import ValidationError
from unittest.mock import MagicMock

from agentclaw.community.adapters.http.devices.router import report_device_status
from agentclaw.community.adapters.http.devices.schemas import (
    ReportDeviceStatusRequest,
)
from agentclaw.community.core.skills_pool.native_confirmation import (
    PoolNativeLayoutConfirmationError,
)


def test_old_status_callback_remains_valid() -> None:
    request = ReportDeviceStatusRequest(
        device_id="device-1",
        status="SUCCEEDED",
        message=None,
    )

    assert request.startup_identity is None
    assert request.layout_initialization is None


def test_layout_initialization_evidence_is_strict_and_optional() -> None:
    request = ReportDeviceStatusRequest(
        device_id="device-1",
        status="SUCCEEDED",
        startup_identity="sandbox-1",
        layout_initialization={
            "actual_engine": "openclaw",
            "actual_layout": "pool",
            "layout_contract_version": "skills-pool-p3-v1",
            "roots_initialized": True,
        },
    )

    assert request.layout_initialization is not None
    assert request.layout_initialization.roots_initialized is True

    with pytest.raises(ValidationError):
        ReportDeviceStatusRequest(
            device_id="device-1",
            status="SUCCEEDED",
            startup_identity="sandbox-1",
            layout_initialization={
                "actual_engine": "openclaw",
                "actual_layout": "pool",
                "layout_contract_version": "skills-pool-p3-v1",
                "roots_initialized": True,
                "physical_paths": ["/home/admin"],
            },
        )


@pytest.mark.asyncio
async def test_layout_confirmation_mismatch_returns_structured_conflict() -> None:
    service = MagicMock()
    service.report_device_status.side_effect = PoolNativeLayoutConfirmationError(
        "layout engine does not match Bot"
    )
    request = ReportDeviceStatusRequest(
        device_id="device-1",
        status="SUCCEEDED",
        startup_identity="sandbox-1",
        layout_initialization={
            "actual_engine": "openclaw",
            "actual_layout": "pool",
            "layout_contract_version": "skills-pool-p3-v1",
            "roots_initialized": True,
        },
    )

    response = await report_device_status(
        req=request,
        service=service,
        authorization="Bearer callback-token",
    )

    assert response.success is False
    assert response.error_code == 40904
    assert response.message == "layout engine does not match Bot"
    assert response.data is None
