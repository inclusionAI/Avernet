"""devices API-lifecycle business flows — data, not tests.

Single source of truth for both executors:
  - 路 A: tests/e2e/test_devices_flows.py via flow_runner.run_flow (TestClient)
  - 路 B: tests/acceptance/devices/ via flow_runner_live.run_flow_live
and for the E3 coverage guard.

devices is READ-ONLY in single box: 8 write endpoints (apply / release /
{id}/connection / exec_shell / callback/alive / callback/status /
callback/bootstrap-auth / batch/env) depend on BaasService which has NO
LocalBaas plugin implementation, so any write call either:
  (a) fails on the LocalDeviceService validation (e.g. apply requires
      migration_path before reaching BaaS), OR
  (b) reaches BaasService and hits real BaaS HTTP (unreachable in single
      box LOCAL).
See docs/singlebox-eval/findings/devices-baas-write-paths-unmocked.md.

LOCAL-reachable read paths covered here:
  - GET /api/v1/devices (list — DB CRUD via DeviceBindingRepository on SQLite)
  - GET /api/v1/devices/{binding_id} (get by PK)
  - GET /api/v1/devices/by-id/{device_id} (get by alt key)

The seeded round-trip (insert ac_entity_device_binding via DatabasePlugin
.session() then GET the seeded row) lives in the e2e test file directly,
NOT as a FlowCase — binding_id is DB-auto-incremented so the path can't be
pinned in the FlowCase upfront.

Each FlowCase covers=["devices"]. Auth via x-user-id; route-A default
"e2e_user", route-B default "e2e_lifecycle_user" — flow expects pin literal
values matching route-A; route-B test passes default_headers={"x-user-id":
"e2e_user"} to override.

Gotcha: FlowRunner.run_flow does NOT interpolate `expect` (only path/body).
"""
from __future__ import annotations

from tests.community.framework.flow import FlowCase, FlowStep

DEVICES_LIFECYCLE_FLOWS: list[FlowCase] = [
    # Flow 1: list with no seed. Wire envelope (Task 0 probe-confirmed):
    # {success: True, error_code: 200, data: {total: 0, items: []}}.
    # No required query params on /api/v1/devices (entity_id defaults to
    # caller's staffId in router).
    FlowCase(
        name="devices-list-empty",
        covers=["devices"],
        steps=[
            FlowStep(method="GET", path="/api/health", expect_status=200,
                     expect={"status": "ok"}),
            FlowStep(
                method="GET",
                path="/api/v1/devices",
                expect_status=200,
                expect={"success": True, "error_code": 200,
                        "data": {"total": 0, "items": []}},
            ),
        ],
    ),
    # Flow 2: get by binding_id 9999 (definitely missing). Probe-confirmed
    # error_code=40402 with message "binding 9999 not found"; the router
    # wraps the not-found case as envelope-200 + success=False (NOT raw HTTP
    # 404).
    FlowCase(
        name="devices-get-binding-missing",
        covers=["devices"],
        steps=[
            FlowStep(
                method="GET",
                path="/api/v1/devices/9999",
                expect_status=200,
                expect={"success": False, "error_code": 40402},
            ),
        ],
    ),
    # Flow 3: get by device_id with non-existent id. Probe-confirmed a
    # DISTINCT error_code from the binding-id lookup: error_code=40405,
    # message "device dev_does_not_exist not found".
    FlowCase(
        name="devices-get-by-device-id-missing",
        covers=["devices"],
        steps=[
            FlowStep(
                method="GET",
                path="/api/v1/devices/by-id/dev_e2e_nonexistent",
                expect_status=200,
                expect={"success": False, "error_code": 40405},
            ),
        ],
    ),
]
