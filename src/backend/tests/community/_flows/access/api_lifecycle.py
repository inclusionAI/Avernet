"""access API-lifecycle business flows — data, not tests.

These FlowCases are the single source of truth for both executors:
  - 路 A: tests/e2e/test_access_flows.py via flow_runner.run_flow (TestClient)
  - 路 B: tests/acceptance/access/ via flow_runner_live.run_flow_live (real backend)
and for the E3 coverage guard (tests/architecture/test_e2e_module_coverage.py).

LOCAL+SQLITE, mock-free. Each FlowCase covers=["access"]. Auth: the runner
injects x-user-id, LocalAuth treats it as staffId, is_operator_allowed=True
so /allow /disallow /upsert_user (which use require_operator) pass through.

The compete-quota branch (PolicyService._try_compete) requires ac_config_item
seed rows which the LOCAL SqliteDB.bootstrap does NOT populate (see
docs/singlebox-eval/09-access.md ⚠️待人工确认点). So we deliberately stay
on the deterministic branches:
  - whitelist on/off (allow → policy=on, disallow → policy=off)
  - user CRUD (UserService)
  - get_quota with no seed → all zeros / empty (确定的"无数据"行为)
The compete-quota branch is documented separately as a finding for future
runs that seed system config.
"""
from __future__ import annotations

from tests.community.framework.flow import FlowCase, FlowStep

ACCESS_LIFECYCLE_FLOWS: list[FlowCase] = [
    # Flow 1: whitelist lifecycle — check default-deny → allow → check allowed →
    # disallow → check denied. Proves the policy table round-trips through the
    # service, and that check() reads policy=on/off branches correctly.
    # The runner's default x-user-id is "e2e_user" — that staffId is both the
    # policy subject (entity_id) and the caller, so /check (which uses the
    # caller's staffId) sees its own policy after allow/disallow.
    FlowCase(
        name="access-whitelist-lifecycle",
        covers=["access"],
        steps=[
            # Liveness — establishes the runner can hit the app.
            FlowStep(method="GET", path="/api/health", expect_status=200,
                     expect={"status": "ok"}),
            # Initial check: no policy row, no compete user, _try_compete fails
            # (effective_quota=0 with no seed) → label=0 (denied).
            FlowStep(
                method="GET",
                path="/api/v1/access/check",
                expect_status=200,
                expect={"success": True, "data": {"label": 0, "staffNo": "e2e_user"}},
            ),
            # Allow the caller — writes policy=on into ac_access_control_policy.
            FlowStep(
                method="POST",
                path="/api/v1/access/allow",
                body={"entity_id": "e2e_user", "entity_type": "staff"},
                expect_status=200,
                expect={"success": True, "data": {"entity_id": "e2e_user", "entity_type": "staff"}},
                extract={"allowed_entity": "data.entity_id"},
            ),
            # Re-check: now policy=on, label=1. expect is literal (FlowRunner does
            # not interpolate expect — path/body do, expect doesn't); the
            # extract→interpolate chain is proven instead by the disallow step
            # below, whose BODY interpolates {allowed_entity} (a broken extract
            # would 422/500 that step, not silently pass).
            FlowStep(
                method="GET",
                path="/api/v1/access/check",
                expect_status=200,
                expect={"success": True, "data": {"label": 1, "staffNo": "e2e_user"}},
            ),
            # Disallow — writes policy=off. Body interpolates {allowed_entity}
            # extracted from the allow step (proves the chain works end-to-end).
            FlowStep(
                method="POST",
                path="/api/v1/access/disallow",
                body={"entity_id": "{allowed_entity}", "entity_type": "staff"},
                expect_status=200,
                expect={"success": True, "data": {"entity_id": "e2e_user"}},
            ),
            # Re-check: policy=off → label=0 (explicit deny, doesn't fall into compete).
            FlowStep(
                method="GET",
                path="/api/v1/access/check",
                expect_status=200,
                expect={"success": True, "data": {"label": 0}},
            ),
        ],
    ),
    # Flow 2: user CRUD lifecycle — upsert → get-by-id → update → get-by-id.
    # Proves UserService write/read round-trips through the DB plugin.
    FlowCase(
        name="access-user-crud-lifecycle",
        covers=["access"],
        steps=[
            FlowStep(
                method="POST",
                path="/api/v1/user",
                body={"user_id": "u_e2e_001", "user_type": "COMPETE", "status": "ACCESS"},
                expect_status=200,
                expect={"success": True, "data": {"userId": "u_e2e_001", "userType": "COMPETE", "status": "ACCESS"}},
                extract={"created_user_id": "data.userId"},
            ),
            # Get back by the extracted id — interpolation proves the chain works.
            FlowStep(
                method="GET",
                path="/api/v1/user/COMPETE/{created_user_id}",
                expect_status=200,
                expect={"success": True, "data": {"userId": "u_e2e_001", "userType": "COMPETE", "status": "ACCESS"}},
            ),
            # List with filter — success=True is the loose check; full content
            # is dynamic across LOCAL/live runs so we don't pin it here.
            FlowStep(
                method="GET",
                path="/api/v1/user?user_type=COMPETE",
                expect_status=200,
                expect={"success": True},
            ),
            # Update status — re-upsert with REFUSE, then get back to confirm.
            FlowStep(
                method="POST",
                path="/api/v1/user",
                body={"user_id": "{created_user_id}", "user_type": "COMPETE", "status": "REFUSE"},
                expect_status=200,
                expect={"success": True, "data": {"status": "REFUSE"}},
            ),
            FlowStep(
                method="GET",
                path="/api/v1/user/COMPETE/{created_user_id}",
                expect_status=200,
                expect={"success": True, "data": {"status": "REFUSE"}},
            ),
        ],
    ),
    # Flow 3: quota endpoint with no seed config — deterministic zero state.
    # In LOCAL+SQLITE without seed rows, get_quota() returns all zeros + "".
    # This pins that "无种子" 是预期 LOCAL 行为 (per 09-access.md ⚠️) so a
    # future seed-rows change CAN'T silently break the existing contract.
    FlowCase(
        name="access-quota-no-seed-defaults",
        covers=["access"],
        steps=[
            FlowStep(
                method="GET",
                path="/api/v1/access/quota",
                expect_status=200,
                expect={
                    "success": True,
                    "data": {
                        "quota": 0,
                        "totalLimit": 0,
                        "activeCount": 0,
                        "effectiveQuota": 0,
                        "updateTime": "",
                    },
                },
            ),
        ],
    ),
]
