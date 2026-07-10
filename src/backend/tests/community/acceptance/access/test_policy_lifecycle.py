"""Route-B acceptance: singlebox local-policy + user CRUD lifecycle.

Starts a real singlebox backend (in-memory SQLite via local_setup.sh),
runs the LocalPolicyService user story through httpx, then independently
re-reads the access endpoints to assert the observable state matches a
baseline.

The singlebox policy binding is all-open and its allow/disallow writes are
no-ops. The user CRUD flow still executes against the real SQLite-backed
UserService. The baseline therefore documents LocalPolicy no-op behavior plus
user CRUD, not an ``ac_access_control_policy`` SQLite row.

Off by default; enable with RUN_ACCEPTANCE=1.
"""
from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from tests.community._flows.access.api_lifecycle import ACCESS_LIFECYCLE_FLOWS
from tests.community.framework.flow_runner_live import run_flow_live

BASELINE_PATH = Path(__file__).parent / "baseline_policy_lifecycle.json"
HEADERS = {"x-user-id": "e2e_user"}


def _run_local_policy_noop_lifecycle(client: httpx.Client) -> dict:
    """Exercise the all-open LocalPolicyService bound by the singlebox profile."""
    for path, body in (
        ("/api/v1/access/check", None),
        ("/api/v1/access/allow", {"entity_id": "e2e_user", "entity_type": "staff"}),
        ("/api/v1/access/check", None),
        ("/api/v1/access/disallow", {"entity_id": "e2e_user", "entity_type": "staff"}),
        ("/api/v1/access/check", None),
    ):
        response = client.get(path) if body is None else client.post(path, json=body)
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["success"] is True, payload
        if body is None:
            assert payload["data"]["label"] == 1, payload
            assert payload["data"]["staffNo"] == "e2e_user", payload

    return payload


@pytest.mark.acceptance
def test_access_local_policy_noop_lifecycle_live(live_backend):
    """Singlebox remains open before and after allow/disallow commands."""
    with httpx.Client(base_url=live_backend, headers=HEADERS, timeout=10.0) as client:
        _run_local_policy_noop_lifecycle(client)


@pytest.mark.acceptance
def test_access_user_crud_lifecycle_live(live_backend, acceptance_fs_root):
    """Run user CRUD flow, then re-read to confirm REFUSE status persisted."""
    flow = next(c for c in ACCESS_LIFECYCLE_FLOWS if c.name == "access-user-crud-lifecycle")
    ctx = run_flow_live(
        flow, base_url=live_backend, fs_root=acceptance_fs_root,
        default_headers=HEADERS,
    )
    assert ctx["created_user_id"] == "u_e2e_001"

    with httpx.Client(
        base_url=live_backend,
        headers={"x-user-id": "e2e_user"},
        timeout=10.0,
    ) as client:
        r = client.get("/api/v1/user/COMPETE/u_e2e_001")
        assert r.status_code == 200
        assert r.json()["data"]["status"] == "REFUSE"


@pytest.mark.acceptance
def test_access_lifecycle_baseline(live_backend, acceptance_fs_root):
    """Pin LocalPolicy no-op and user CRUD observable state to the baseline.

    This is deliberately not a policy-row persistence assertion: singlebox
    binds LocalPolicyService, which is all-open and makes writes no-ops.

    First-run capture mode: if baseline doesn't exist or is empty, write the
    current snapshot and SKIP the comparison (with a message). Reviewer
    commits the baseline; next run does the real diff.
    """
    with httpx.Client(base_url=live_backend, headers=HEADERS, timeout=10.0) as client:
        _run_local_policy_noop_lifecycle(client)

    case = next(c for c in ACCESS_LIFECYCLE_FLOWS if c.name == "access-user-crud-lifecycle")
    run_flow_live(
        case, base_url=live_backend, fs_root=acceptance_fs_root,
        default_headers=HEADERS,
    )

    # Dump observable state via GET endpoints (not direct DB — keeps the
    # baseline at the public-API contract level, which is what we ship).
    with httpx.Client(
        base_url=live_backend,
        headers=HEADERS,
        timeout=10.0,
    ) as client:
        check = client.get("/api/v1/access/check").json()
        users = client.get("/api/v1/user?user_type=COMPETE").json()
        quota = client.get("/api/v1/access/quota").json()

    snapshot = {
        "check_after_disallow": {
            "label": check["data"]["label"],
            "staffNo": check["data"]["staffNo"],
        },
        "users_compete": [
            {"userId": u["userId"], "userType": u["userType"], "status": u["status"]}
            for u in users["data"]
        ],
        "quota_local_unlimited": {
            "quota": quota["data"]["quota"],
            "totalLimit": quota["data"]["totalLimit"],
            "activeCount": quota["data"]["activeCount"],
            "effectiveQuota": quota["data"]["effectiveQuota"],
            "updateTime": quota["data"]["updateTime"],
        },
    }

    # First-run capture mode: empty baseline → write + skip compare.
    if not BASELINE_PATH.exists() or BASELINE_PATH.stat().st_size == 0:
        BASELINE_PATH.write_text(json.dumps(snapshot, indent=2, sort_keys=True) + "\n")
        pytest.skip(
            f"baseline captured at {BASELINE_PATH}; review + commit it, "
            "next run will diff against it"
        )

    expected = json.loads(BASELINE_PATH.read_text())
    assert snapshot == expected, (
        f"access lifecycle DB state diverged from baseline.\n"
        f"  expected: {json.dumps(expected, indent=2, sort_keys=True)}\n"
        f"  actual:   {json.dumps(snapshot, indent=2, sort_keys=True)}"
    )
