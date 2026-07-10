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
import time
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


def wait_device_active(
    client: httpx.Client,
    binding_id: int,
    *,
    timeout_sec: int = 180,
) -> dict:
    """Wait for the real Backend -> BaaS publish flow to activate a binding."""
    deadline = time.monotonic() + timeout_sec
    last: dict | None = None
    while time.monotonic() < deadline:
        response = client.get(f"/api/v1/devices/{binding_id}")
        if response.status_code == 200:
            payload = response.json()
            if payload.get("success") is True:
                last = payload["data"]
                if last["status"] == "ACTIVE":
                    return last
                assert last["status"] != "FAILED", last
            else:
                # Bot readiness and the BaaS alive callback are asynchronous. A
                # business error may briefly race the SQLite callback transaction.
                last = payload
        else:
            # Transport/framework errors are retried as well. The final response
            # remains visible in the timeout failure instead of being swallowed.
            last = {"status_code": response.status_code, "text": response.text}
        time.sleep(2)
    pytest.fail(f"device binding {binding_id} did not become active; last={last}")


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

        detail = wait_device_active(client, binding_id)
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

        connection_by_bot = client.get(
            f"/api/v1/devices/bots/{bot['bot_id']}/connection"
        ).json()
        assert connection_by_bot["success"] is False
        assert connection_by_bot["error_code"] == 40403
        assert "BotPublishRepository not available" in connection_by_bot["message"]

        config_payload = {
            "singlebox": {
                "module": "devices",
                "story": "device-filesystem-config-roundtrip",
            }
        }
        saved_config = assert_success(
            client.put(
                f"/api/bots/{bot['bot_id']}/engine-config",
                json=config_payload,
            )
        )["data"]
        assert saved_config == config_payload
        read_config = assert_success(
            client.get(f"/api/bots/{bot['bot_id']}/engine-config")
        )["data"]
        assert read_config == config_payload

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

        inventory = assert_success(
            client.get(
                "/api/v1/devices/provider-inventory",
                params={"entity_id": user_id, "entity_type": "staff"},
            )
        )["data"]
        assert inventory["total"] == 1
        assert inventory["scanned"] == 1
        assert inventory["by_provider"]["local"]["total"] == 1

        bootstrap_auth = assert_success(
            client.post(
                "/api/v1/devices/callback/bootstrap-auth",
                json={
                    "device_id": device_id,
                    "bot_id": bot["bot_id"],
                    "owner_id": user_id,
                },
            )
        )["data"]
        assert bootstrap_auth["agent_code"] == f"local_{bot['bot_id']}"

        invalid_alive = client.post(
            "/api/v1/devices/callback/alive",
            headers={"Authorization": "Bearer invalid-device-token"},
            json={"device_id": device_id},
        ).json()
        assert invalid_alive["success"] is False
        assert invalid_alive["error_code"] == 40302

        invalid_status = client.post(
            "/api/v1/devices/callback/status",
            headers={"Authorization": "Bearer invalid-device-token"},
            json={
                "device_id": device_id,
                "status": "SUCCEEDED",
                "message": "untrusted callback must not mutate device state",
            },
        ).json()
        assert invalid_status["success"] is False
        assert invalid_status["error_code"] == 40302

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

        instances_by_bot = client.get(
            f"/api/v1/devices/bots/{bot['bot_id']}/instances"
        ).json()
        assert instances_by_bot["success"] is False
        assert instances_by_bot["error_code"] == 40403
        assert "BotPublishRepository not available" in instances_by_bot["message"]

        restart = client.post(
            f"/api/v1/devices/{binding_id}/restart",
            json={"device_uuid": device_id},
        ).json()
        assert restart["success"] is False
        assert restart["error_code"] == 40403
        assert "does not support instances query" in restart["message"]

        env_update = assert_success(
            client.post(
                "/api/v1/devices/batch/env",
                json={"binding_ids": [binding_id, 999999], "env": "dev"},
            )
        )["data"]
        assert env_update == {
            "total": 2,
            "updated": 1,
            "updated_ids": [binding_id],
        }

        released = assert_success(
            client.post(
                f"/api/v1/devices/{binding_id}/release",
                json={"release_reason": "singlebox device lifecycle complete"},
            )
        )["data"]
        assert released["status"] == "RELEASED"

        readback = assert_success(client.get(f"/api/v1/devices/{binding_id}"))["data"]
        assert readback["status"] == "RELEASED"

        reapplied = assert_success(
            client.post(
                "/api/v1/devices",
                json={
                    "apply_reason": "restore released singlebox device",
                    "entity_id": user_id,
                    "entity_type": "staff",
                    "bot_id": bot["bot_id"],
                    "engine": "openclaw",
                },
            )
        )["data"]
        reapplied_binding_id = int(reapplied["id"])
        assert reapplied["device_provider"] == "local"
        active_reapplied = wait_device_active(client, reapplied_binding_id)
        assert active_reapplied["device_id"] == reapplied["device_id"]

        final_release = assert_success(
            client.post(
                f"/api/v1/devices/{reapplied_binding_id}/release",
                json={"release_reason": "reapplied device acceptance complete"},
            )
        )["data"]
        assert final_release["status"] == "RELEASED"
