"""devices e2e business flows (real endpoints, LOCAL+SQLITE, no mocks for DB source).

DEVICES_LIFECYCLE_FLOWS is the single source of truth for both this runner
and the E3 coverage guard.

devices is read-only in single box. This file additionally exercises a seeded
round-trip: insert an ac_entity_device_binding row directly via DatabasePlugin
.session() raw SQL, then GET it back via list / binding_id / device_id. The
seeded round-trip is NOT a FlowCase because binding_id is auto-incremented
(can't be pinned upfront in static flow data).

Write paths (apply/release/connection/exec_shell/callback/batch) are NOT
exercised — they require BaaS HTTP without a LocalBaas plugin. See
findings/devices-baas-write-paths-unmocked.md for the merged scope note.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from agentclaw.community.plugin_api.database import DatabasePlugin
from tests.community._flows.devices.api_lifecycle import DEVICES_LIFECYCLE_FLOWS
from tests.community.framework.flow_runner import run_flow


def _seed_device_binding(
    world,
    *,
    entity_id: str,
    device_id: str,
    device_provider: str = "local",
    status: str = "ACTIVE",
) -> int:
    """Insert one ac_entity_device_binding row; return its auto-incremented id.

    Uses raw SQL via DatabasePlugin.session() — keeps the seed concern at the
    schema level. The route-A runner injects x-user-id=e2e_user, so we seed
    entity_id="e2e_user" to match (the list endpoint's default filter is the
    caller's staffId).

    Non-null columns populated: entity_id, entity_type, device_id,
    device_provider, env, device_props, status, applied_by, gmt_create,
    gmt_modified. apply_reason / release_reason / released_by / released_at /
    last_alive_at are nullable. See plugins/local/sqlite_models.py for the
    canonical schema.
    """
    plugin = world.get(DatabasePlugin)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    with plugin.session() as s:
        s.execute(text("""
            INSERT INTO ac_entity_device_binding
              (entity_id, entity_type, device_id, device_provider, env,
               device_props, status, applied_by, gmt_create, gmt_modified)
            VALUES (:entity_id, :entity_type, :device_id, :device_provider,
                    :env, :device_props, :status, :applied_by,
                    :gmt_create, :gmt_modified)
        """), {
            "entity_id": entity_id,
            "entity_type": "staff",
            "device_id": device_id,
            "device_provider": device_provider,
            "env": "dev",
            "device_props": "{}",
            "status": status,
            "applied_by": entity_id,
            "gmt_create": now,
            "gmt_modified": now,
        })
        s.commit()
        row = s.execute(
            text("SELECT id FROM ac_entity_device_binding WHERE device_id = :did"),
            {"did": device_id},
        ).first()
        return int(row[0])


@pytest.mark.e2e
@pytest.mark.parametrize("case", DEVICES_LIFECYCLE_FLOWS, ids=lambda c: c.name)
def test_devices_flow(case, app_with_testing_modules, world):
    """3 read-only flows: list empty / get binding 404 / by-id 404."""
    ctx = run_flow(case, app_with_testing_modules, world)
    assert ctx is not None


@pytest.mark.e2e
def test_devices_seeded_get_roundtrip(app_with_testing_modules, world):
    """Seed an ac_entity_device_binding row; assert list/get-by-id/by-device-id
    all surface it. Not a FlowCase because binding_id is auto-incremented and
    can't be pinned in static flow data."""
    binding_id = _seed_device_binding(
        world,
        entity_id="e2e_user",
        device_id="dev_e2e_001",
        device_provider="local",
        status="ACTIVE",
    )
    assert binding_id > 0

    client = TestClient(app_with_testing_modules, headers={"x-user-id": "e2e_user"})

    # 1) GET by binding_id PK — must surface the seeded row.
    r = client.get(f"/api/v1/devices/{binding_id}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["success"] is True, body
    assert body["data"]["device_id"] == "dev_e2e_001"

    # 2) GET by device_id alt key — must surface the same row.
    r = client.get("/api/v1/devices/by-id/dev_e2e_001")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["success"] is True, body
    assert body["data"]["id"] == binding_id

    # 3) GET list — envelope is data: {total, items} (probe-confirmed).
    r = client.get("/api/v1/devices")
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert body["data"]["total"] >= 1, body
    assert any(e.get("id") == binding_id for e in body["data"]["items"]), body
