"""Route-B acceptance: access full whitelist + user CRUD lifecycle.

Starts a real singlebox backend (in-memory SQLite via local_setup.sh),
runs ACCESS_LIFECYCLE_FLOWS end-to-end through httpx, then independently
re-reads the access endpoints to assert the DB state matches a baseline.

access has NO filesystem artifacts (pure SQLite rows), so the baseline is a
JSON snapshot of the observable DB state via the GET endpoints, not a tree
file. The baseline is in git; any change to "what's persisted by the
lifecycle" must update the baseline explicitly.

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


@pytest.mark.acceptance
def test_access_whitelist_lifecycle_live(live_backend, acceptance_fs_root):
    """Run whitelist-lifecycle against real backend, then assert DB state."""
    flow = next(c for c in ACCESS_LIFECYCLE_FLOWS if c.name == "access-whitelist-lifecycle")
    ctx = run_flow_live(
        flow, base_url=live_backend, fs_root=acceptance_fs_root,
        default_headers={"x-user-id": "e2e_user"},
    )
    assert "allowed_entity" in ctx

    # Independent re-read: the lifecycle ends with disallow, so /check must
    # still report deny — proves the disallow persisted to the live SQLite
    # and isn't just a per-request fake.
    with httpx.Client(
        base_url=live_backend,
        headers={"x-user-id": "e2e_user"},
        timeout=10.0,
    ) as client:
        check_resp = client.get("/api/v1/access/check")
        assert check_resp.status_code == 200
        assert check_resp.json()["data"]["label"] == 0


@pytest.mark.acceptance
def test_access_user_crud_lifecycle_live(live_backend, acceptance_fs_root):
    """Run user CRUD flow, then re-read to confirm REFUSE status persisted."""
    flow = next(c for c in ACCESS_LIFECYCLE_FLOWS if c.name == "access-user-crud-lifecycle")
    ctx = run_flow_live(
        flow, base_url=live_backend, fs_root=acceptance_fs_root,
        default_headers={"x-user-id": "e2e_user"},
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
    """Run both lifecycles, dump observable DB state, compare to baseline.

    Physical-artifact analogue for a module without filesystem artifacts:
    the baseline JSON pins exactly what's observable via the public API after
    the lifecycle. Any change to lifecycle semantics must update the baseline.

    First-run capture mode: if baseline doesn't exist or is empty, write the
    current snapshot and SKIP the comparison (with a message). Reviewer
    commits the baseline; next run does the real diff.
    """
    for case_name in ("access-whitelist-lifecycle", "access-user-crud-lifecycle"):
        case = next(c for c in ACCESS_LIFECYCLE_FLOWS if c.name == case_name)
        run_flow_live(
            case, base_url=live_backend, fs_root=acceptance_fs_root,
            default_headers={"x-user-id": "e2e_user"},
        )

    # Dump observable state via GET endpoints (not direct DB — keeps the
    # baseline at the public-API contract level, which is what we ship).
    with httpx.Client(
        base_url=live_backend,
        headers={"x-user-id": "e2e_user"},
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
        "quota_no_seed": {
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
