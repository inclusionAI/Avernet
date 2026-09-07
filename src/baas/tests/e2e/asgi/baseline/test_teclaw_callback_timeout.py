"""E2E test for the TeClaw async-callback timeout non-goal.

Mirrors ``test_async_device_hooks_timeout.py``: seeds a device with
``PENDING`` status and ``task_status="RUNNING"``, does NOT call the callback,
and asserts the device ``status`` remains ``PENDING`` after a short sleep.

Orphan detection is explicitly a Non-Goal per design.md — this test documents
that the system does NOT auto-transition a device without a callback. The
follow-up sweep change will handle orphans.

Requires:
- ASGI TestClient transport (it-sqlite overlay)
"""

import asyncio
from typing import TYPE_CHECKING, Any

import pytest

from tests.e2e.asgi.baseline.test_teclaw_callback_happy_path import (
    _get_device,
    _seed_teclaw_device,
)

if TYPE_CHECKING:
    from secbaas.community.bootstrap import ApplicationContainer

pytestmark = [pytest.mark.e2e_asgi]


class TestTeclawCallbackTimeout:
    """Orphan detection is a Non-Goal — device stays PENDING without callback."""

    @pytest.mark.asyncio
    async def test_teclaw_callback_never_arrives_device_stays_pending(
        self,
        bootstrap_init: "ApplicationContainer",
        unique_id: str,
    ) -> None:
        """A device seeded with PENDING + task_status=RUNNING stays PENDING
        when no callback arrives. The system does NOT auto-transition or
        crash — orphan detection is a Non-Goal per design.md."""
        device_uuid = f"DEVICE-teclaw-timeout-{unique_id}"
        _seed_teclaw_device(
            bootstrap_init,
            device_uuid=device_uuid,
            status="PENDING",
            task_id="t-timeout",
            task_status="RUNNING",
            operation="CREATE",
            version=1,
        )

        await asyncio.sleep(0.1)

        device = _get_device(bootstrap_init, device_uuid)
        assert device is not None, "Seeded device not found"
        assert device.status == "PENDING", (
            f"Device should remain PENDING when no callback arrives "
            f"(orphan detection is a Non-Goal); got: {device.status}"
        )
        props: dict[str, Any] = device.provider_device_props or {}
        assert props.get("task_status") == "RUNNING", (
            f"task_status should remain 'RUNNING'; got: {props.get('task_status')}"
        )
