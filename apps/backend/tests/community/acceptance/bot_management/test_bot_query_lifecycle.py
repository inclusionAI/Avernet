"""Route-B acceptance: bot_management read-only query lifecycle on live backend.

Starts a real singlebox backend, retains the check-name/get-missing flows, and
asserts an isolated user scope is empty.

bot_management is the largest module (30+ HTTP endpoints, 3000+ line BotService).
Single box covers ONLY the LOCAL-reachable core CRUD read paths backed by the
unified BotRepository on SQLite. POST /api/bots (Passport + device-allocation
half-baked) and 4 external-dep paths (BCN onboard / DIMA workspace / downstream
sync ECB+BCSFuse / DataInitService cold-start) are NOT exercised — see
findings/bot_management-external-deps-unmocked.md.

Acceptance covers the scoped empty/no-data contract:
  - one dedicated user scope returns empty {total: 0, items: []}
  - check-name with non-existent name → available (exists=False)
  - get missing bot_id → success=False, error_code=404

Off by default; enable with RUN_ACCEPTANCE=1.
"""
from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from tests.community._flows.bot_management.api_lifecycle import BOT_MANAGEMENT_LIFECYCLE_FLOWS
from tests.community.framework.flow_runner_live import run_flow_live

BASELINE_PATH = Path(__file__).parent / "baseline_bot_query.json"
SCOPED_USER_ID = "acceptance_bot_query_user"
HEADERS = {"x-user-id": SCOPED_USER_ID}
SCOPED_LIST_PARAMS = {
    "entity_id": SCOPED_USER_ID,
    "entity_type": "staff",
    "page": 1,
    "page_size": 20,
}


def _list_scoped_bots(client: httpx.Client) -> dict:
    response = client.get("/api/bots", params=SCOPED_LIST_PARAMS)
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["success"] is True, payload
    return payload


@pytest.mark.acceptance
def test_bot_management_list_empty_live(live_backend):
    """List is empty in a dedicated user/entity scope despite seeded live bots."""
    with httpx.Client(base_url=live_backend, headers=HEADERS, timeout=10.0) as client:
        payload = _list_scoped_bots(client)
    assert payload["data"] == {"total": 0, "items": []}


@pytest.mark.acceptance
def test_bot_management_check_name_available_live(live_backend, acceptance_fs_root):
    """Check name with a never-seeded bot_name → available=true."""
    flow = next(c for c in BOT_MANAGEMENT_LIFECYCLE_FLOWS if c.name == "bot_management-check-name-available")
    ctx = run_flow_live(
        flow, base_url=live_backend, fs_root=acceptance_fs_root,
        default_headers=HEADERS,
    )
    assert ctx is not None


@pytest.mark.acceptance
def test_bot_management_get_missing_live(live_backend, acceptance_fs_root):
    """GET /api/bots/<nonexistent> returns envelope-wrapped 404."""
    flow = next(c for c in BOT_MANAGEMENT_LIFECYCLE_FLOWS if c.name == "bot_management-get-bot-missing")
    ctx = run_flow_live(
        flow, base_url=live_backend, fs_root=acceptance_fs_root,
        default_headers=HEADERS,
    )
    assert ctx is not None


@pytest.mark.acceptance
def test_bot_management_lifecycle_baseline(live_backend, acceptance_fs_root):
    """Pin isolated user-scope observable state to JSON baseline.

    First-run captures + skips; subsequent runs diff with full equality.
    """
    with httpx.Client(base_url=live_backend, headers=HEADERS, timeout=10.0) as client:
        list_resp = _list_scoped_bots(client)
        check_resp = client.get("/api/bots/check/name?bot_name=NonExistent_Baseline_Bot").json()
        get_missing_resp = client.get("/api/bots/bot_baseline_missing").json()

    snapshot = {
        "list_no_seed": {
            "success": list_resp["success"],
            "total": list_resp["data"]["total"],
            "entries_count": len(list_resp["data"]["items"]),
        },
        "check_name_available": {
            "success": check_resp["success"],
            "exists": check_resp["data"]["exists"],
            "bot_name": check_resp["data"]["bot_name"],
        },
        "get_missing": {
            "success": get_missing_resp["success"],
            "error_code": get_missing_resp["error_code"],
            "data_is_none": get_missing_resp["data"] is None,
        },
    }

    if not BASELINE_PATH.exists() or BASELINE_PATH.stat().st_size == 0:
        BASELINE_PATH.write_text(json.dumps(snapshot, indent=2, sort_keys=True) + "\n")
        pytest.skip(
            f"baseline captured at {BASELINE_PATH}; review + commit it, next run will diff"
        )

    expected = json.loads(BASELINE_PATH.read_text())
    assert snapshot == expected, (
        f"bot_management no-data baseline diverged.\n"
        f"  expected: {json.dumps(expected, indent=2, sort_keys=True)}\n"
        f"  actual:   {json.dumps(snapshot, indent=2, sort_keys=True)}"
    )
