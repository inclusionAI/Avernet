"""Route-B acceptance: devices read-only query lifecycle on live backend.

Starts a real singlebox backend (in-memory SQLite via local_setup.sh),
runs the 3 read-only flows + asserts no-data state matches baseline.

devices write paths (apply/release/connection/exec_shell/callback/batch — 8
endpoints) all depend on BaasService HTTP without a LocalBaas plugin — out
of scope for single box (see findings/devices-baas-write-paths-unmocked.md).

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
