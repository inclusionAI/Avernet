"""Route-B acceptance: bot_management read-only query lifecycle on live backend.

Starts a real --local backend, runs the 3 read-only flows + asserts no-data
state matches baseline.

bot_management is the largest module (30+ HTTP endpoints, 3000+ line BotService).
Single box covers ONLY the LOCAL-reachable core CRUD read paths backed by the
unified BotRepository on SQLite. POST /api/bots (Passport + device-allocation
half-baked) and 4 external-dep paths (BCN onboard / DIMA workspace / downstream
sync ECB+BCSFuse / DataInitService cold-start) are NOT exercised — see
findings/bot_management-external-deps-unmocked.md.

Acceptance covers the empty/no-data contract:
  - list returns empty {total: 0, items: []}
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
HEADERS = {"x-user-id": "e2e_user"}


@pytest.mark.acceptance
def test_bot_management_list_empty_live(live_backend, acceptance_fs_root):
    """List empty on a fresh LOCAL backend with no bots seeded."""
    flow = next(c for c in BOT_MANAGEMENT_LIFECYCLE_FLOWS if c.name == "bot_management-list-empty")
    ctx = run_flow_live(
        flow, base_url=live_backend, fs_root=acceptance_fs_root,
        default_headers=HEADERS,
    )
    assert ctx is not None


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
    """Pin LOCAL no-seed observable state to JSON baseline.

    First-run captures + skips; subsequent runs diff with full equality.
    """
    with httpx.Client(base_url=live_backend, headers=HEADERS, timeout=10.0) as client:
        list_resp = client.get("/api/bots").json()
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
