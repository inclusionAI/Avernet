"""Route-B acceptance: devices query and local-provider lifecycle.

Starts a real singlebox backend (in-memory SQLite via local_setup.sh),
runs the 3 read-only flows, asserts the no-data baseline, and creates one real
personal bot through Backend -> BaaS before exercising its device binding.

The open-source singlebox records the created binding as provider ``local``.
Connection and release are supported; instance-list and restart are
BaaS/Teclaw-only and must return the documented capability error rather than
silently pretending that a local binding is a remote multi-instance runtime.

Acceptance covers only the empty/no-data contract:
  - list returns empty {total: 0, items: []}
  - get binding 9999 → success=False, error_code=40402
  - get by-id missing → success=False, error_code=40405

Off by default; enable with RUN_ACCEPTANCE=1.
"""
from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from tests.community._flows.devices.api_lifecycle import DEVICES_LIFECYCLE_FLOWS
from tests.community.acceptance._fixtures.live_personal_bot import (
    assert_success,
    create_live_personal_bot,
    fresh_id,
)
from tests.community.framework.flow_runner_live import run_flow_live

BASELINE_PATH = Path(__file__).parent / "baseline_device_query.json"
HEADERS = {"x-user-id": "e2e_user"}


@pytest.mark.acceptance
def test_devices_list_empty_live(live_backend, acceptance_fs_root):
    """List empty on a fresh LOCAL backend with no device bindings seeded."""
    flow = next(c for c in DEVICES_LIFECYCLE_FLOWS if c.name == "devices-list-empty")
    ctx = run_flow_live(
        flow, base_url=live_backend, fs_root=acceptance_fs_root,
        default_headers=HEADERS,
    )
    assert ctx is not None


@pytest.mark.acceptance
def test_devices_get_missing_returns_40402_live(live_backend, acceptance_fs_root):
    """GET /api/v1/devices/{nonexistent_id} returns 40402 (binding-not-found)."""
    with httpx.Client(base_url=live_backend, headers=HEADERS, timeout=10.0) as client:
        r = client.get("/api/v1/devices/9999")
        assert r.status_code == 200
        body = r.json()
        assert body["success"] is False
        assert body["error_code"] == 40402


@pytest.mark.acceptance
def test_devices_by_device_id_missing_live(live_backend, acceptance_fs_root):
    """GET /api/v1/devices/by-id/<nonexistent> returns 40405 (device-not-found)."""
    with httpx.Client(base_url=live_backend, headers=HEADERS, timeout=10.0) as client:
        r = client.get("/api/v1/devices/by-id/dev_does_not_exist")
        assert r.status_code == 200
        body = r.json()
        assert body["success"] is False
        assert body["error_code"] == 40405


@pytest.mark.acceptance
def test_devices_lifecycle_baseline(live_backend, acceptance_fs_root):
    """Pin LOCAL no-seed observable state to JSON baseline.

    First-run captures + skips; subsequent runs diff with full equality.
    """
    with httpx.Client(base_url=live_backend, headers=HEADERS, timeout=10.0) as client:
        list_resp = client.get("/api/v1/devices").json()
        get_missing_resp = client.get("/api/v1/devices/9999").json()
        by_id_missing_resp = client.get("/api/v1/devices/by-id/dev_baseline_missing").json()

    # List envelope confirmed by Task 0 probe: data: {total, items}.
    snapshot = {
        "list_no_seed": {
            "success": list_resp["success"],
            "total": list_resp["data"]["total"],
            "entries_count": len(list_resp["data"]["items"]),
        },
        "get_missing": {
            "success": get_missing_resp["success"],
            "error_code": get_missing_resp["error_code"],
        },
        "by_id_missing": {
            "success": by_id_missing_resp["success"],
            "error_code": by_id_missing_resp["error_code"],
        },
    }

    if not BASELINE_PATH.exists() or BASELINE_PATH.stat().st_size == 0:
        BASELINE_PATH.write_text(json.dumps(snapshot, indent=2, sort_keys=True) + "\n")
        pytest.skip(
            f"baseline captured at {BASELINE_PATH}; review + commit it, next run will diff"
        )

    expected = json.loads(BASELINE_PATH.read_text())
    assert snapshot == expected, (
        f"devices no-data baseline diverged.\n"
        f"  expected: {json.dumps(expected, indent=2, sort_keys=True)}\n"
        f"  actual:   {json.dumps(snapshot, indent=2, sort_keys=True)}"
    )


@pytest.mark.acceptance
def test_device_live_local_provider_lifecycle(live_backend):
    """Create a real local runtime, inspect its device, then release it."""
    user_id = fresh_id("device_owner")
    headers = {"x-user-id": user_id}

    with httpx.Client(base_url=live_backend, headers=headers, timeout=60.0) as client:
        bot = create_live_personal_bot(
            client,
            user_id=user_id,
            bot_name_prefix="Device Acceptance",
            bot_desc="device live lifecycle acceptance bot",
        )
        binding_id = int(bot["binding_id"])
        device_id = str(bot["device_id"])

        detail = assert_success(client.get(f"/api/v1/devices/{binding_id}"))["data"]
        assert detail["entity_id"] == user_id
        assert detail["device_id"] == device_id
        assert detail["device_provider"] == "local"
        assert detail["status"] == "ACTIVE"

        by_device_id = assert_success(
            client.get(f"/api/v1/devices/by-id/{device_id}")
        )["data"]
        assert by_device_id["id"] == binding_id

        listed = assert_success(client.get("/api/v1/devices"))["data"]
        assert any(item["id"] == binding_id for item in listed["items"]), listed

        connection = assert_success(
            client.get(f"/api/v1/devices/{binding_id}/connection")
        )["data"]
        assert connection["available"] is True
        assert connection["url"]

        connectable = assert_success(
            client.get(
                "/api/v1/devices/connectable",
                params={
                    "entity_id": user_id,
                    "entity_type": "staff",
                    "with_connection": True,
                },
            )
        )["data"]
        assert any(item["id"] == binding_id for item in connectable["items"]), connectable

        with httpx.Client(
            base_url=live_backend,
            headers={"x-user-id": fresh_id("device_other")},
            timeout=30.0,
        ) as other_client:
            forbidden = other_client.get(f"/api/v1/devices/{binding_id}").json()
        assert forbidden["success"] is False
        assert forbidden["error_code"] == 403

        instances = client.get(f"/api/v1/devices/{binding_id}/instances").json()
        assert instances["success"] is False
        assert instances["error_code"] == 40403
        assert "does not support instances query" in instances["message"]

        restart = client.post(
            f"/api/v1/devices/{binding_id}/restart",
            json={"device_uuid": device_id},
        ).json()
        assert restart["success"] is False
        assert restart["error_code"] == 40403
        assert "does not support instances query" in restart["message"]

        released = assert_success(
            client.post(
                f"/api/v1/devices/{binding_id}/release",
                json={"release_reason": "singlebox device lifecycle complete"},
            )
        )["data"]
        assert released["status"] == "RELEASED"

        readback = assert_success(client.get(f"/api/v1/devices/{binding_id}"))["data"]
        assert readback["status"] == "RELEASED"
